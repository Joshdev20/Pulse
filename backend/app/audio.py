"""Make a voice note playable wherever it is opened.

A recorder can only produce what its own engine can encode. Chrome and Firefox
write WebM/Opus; Safari writes MP4/AAC and cannot decode WebM at all. So a note
recorded on Chrome and opened on an iPhone is bytes the reader has no codec for,
which the player can only report as unavailable — the message arrives, but it
cannot be heard.

The fix is to store one format every reader can decode. AAC in an MP4 container
is the only pair that covers every engine that can record: Chrome, Firefox,
Safari, and Android all play it, while Opus in WebM does not reach iOS and
Vorbis in Ogg reaches neither iOS nor Safari. A note that is already AAC is left
exactly as recorded, so the common iOS path costs no transcode at all.

Like `video.py`, this shells out to ffmpeg — there is no pure-Python AAC
encoder — and prefers the binary `imageio-ffmpeg` ships over `PATH`, because the
free instance has none and a missing transcode is the bug this exists to fix.
Everything here is best-effort: if ffmpeg is unavailable, cannot read the input,
or runs out of time, the original bytes are returned unchanged. A note that
might not play on every device is better than a note that failed to send.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from .storage import SNIFF_BYTES

logger = logging.getLogger("pulse.audio")

# Mono speech at 48kHz. Opus decodes at 48k, so resampling anything else up to it
# costs nothing in quality and keeps one output shape for every input.
TARGET_SAMPLE_RATE = 48000
# 32kbps mono Opus/AAC is transparent for speech and keeps a minute of audio in
# the low hundreds of kilobytes, which matters because the bytes live in a
# database row. AAC-LC at this rate is what voice messages elsewhere use.
TARGET_BITRATE = "32k"

# A voice note is at most a couple of minutes, so a transcode that has not
# finished by now is one whose output is not worth holding up the send for.
TRANSCODE_TIMEOUT_SECONDS = 30

# The content type and extension of the one format everything can play.
TARGET_CONTENT_TYPE = "audio/mp4"
TARGET_EXTENSION = "m4a"


def _bundled_ffmpeg() -> str | None:
    """Path to the ffmpeg that `imageio-ffmpeg` ships, or None if unavailable."""
    try:
        import imageio_ffmpeg
    except ImportError:  # pragma: no cover - the dependency is declared
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - a broken wheel must not break uploads
        return None


def _ffmpeg() -> str | None:
    """The ffmpeg to use: the bundled one, then the system one, then none."""
    return _bundled_ffmpeg() or shutil.which("ffmpeg")


def ffmpeg_available() -> bool:
    """Whether audio can be transcoded here at all."""
    return _ffmpeg() is not None


def is_universal(data: bytes, content_type: str) -> bool:
    """Whether a clip is already the one format every reader can decode.

    The container alone does not answer this. Chrome reports `audio/mp4` for a
    recording and writes Opus *inside* it — a file that is an MP4 and still
    unplayable on iOS. So the test is on the codec: an MP4 whose sample entry is
    `mp4a` carries AAC, which is the universal case, and anything else is
    converted. `mp4a` sits in the `stsd` box near the front of a streaming
    layout, so it is inside the inspected window.
    """
    if content_type != TARGET_CONTENT_TYPE:
        return False
    return b"mp4a" in data[:SNIFF_BYTES]


def to_universal(data: bytes) -> tuple[bytes, str] | None:
    """``data`` re-encoded as AAC/MP4, with its content type, or None.

    None means the transcode did not happen — no ffmpeg, an input it cannot
    read, or a timeout. The caller keeps the original bytes in that case, so
    this never costs an upload.
    """
    binary = _ffmpeg()
    if binary is None:
        return None

    with tempfile.TemporaryDirectory(prefix="pulse-audio-") as workdir:
        source = Path(workdir) / "in"
        target = Path(workdir) / "out.m4a"
        try:
            source.write_bytes(data)
        except OSError:
            return None

        command = [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            # One mono track: a voice note is one person speaking, and a second
            # channel would only double the bytes.
            "-ac",
            "1",
            "-ar",
            str(TARGET_SAMPLE_RATE),
            "-c:a",
            "aac",
            "-b:a",
            TARGET_BITRATE,
            # `+faststart` puts the index at the front, so a reader can start
            # playing before the whole clip has arrived — the same layout the
            # recorder would have produced.
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(target),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                timeout=TRANSCODE_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            logger.info("Audio transcode failed: %s", exc)
            return None

        try:
            converted = target.read_bytes()
        except OSError:
            return None

    # A zero-byte file means ffmpeg exited 0 having written nothing, which would
    # otherwise be stored as a note that never plays.
    if not converted:
        return None
    return converted, TARGET_CONTENT_TYPE
