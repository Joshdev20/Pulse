"""Produce a video's preview frame on the server.

A tile must show a real frame without downloading the whole clip. The browser
captures one at upload and sends it with the video, which is the fast path and
covers everything uploaded since posters existed. This module is the server-side
counterpart, for the videos that path cannot reach: clips uploaded before it,
and a client whose capture failed (an unsupported codec, a browser that will not
decode a frame).

There is no pure-Python way to decode H.264, so this shells out to ffmpeg. Two
sources are tried in order:

1. `ffmpeg` on `PATH`, if the host provides it.
2. The binary bundled by `imageio-ffmpeg`, a pinned dependency.

The bundled copy is preferred as a guarantee rather than a convenience: the free
instance the app runs on has no ffmpeg, and a poster that silently never appears
is exactly the bug this module exists to fix. It is roughly 80MB in the image,
which is a cost worth paying once at build time to stop paying it on every view.

Everything here is best-effort. A poster is an enhancement, so a missing binary,
a codec ffmpeg cannot read, or a timeout returns None and the caller stores the
video without one — never an error.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("pulse.video")

# Longest edge of a generated poster. Matches the client capture so a stored
# frame is the same size whichever side produced it.
POSTER_MAX_EDGE = 640

# JPEG quality, on ffmpeg's 2-31 scale where lower is better. 4 is visually
# clean at thumbnail size and lands in the tens of kilobytes.
POSTER_QUALITY = 4

# Give up on a decode that has not finished in this long. A clip that needs
# longer is one whose poster is not worth holding up an upload for.
EXTRACT_TIMEOUT_SECONDS = 20

# The seek point, in seconds. Frame zero of many uploads is a black fade-in, so
# a little way in is more likely to show actual content.
SEEK_SECONDS = 0.5


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
    """The ffmpeg to use: the bundled one, then the system one, then none.

    The bundled binary is tried first so behaviour does not change with whatever
    happens to be installed on the host.
    """
    return _bundled_ffmpeg() or shutil.which("ffmpeg")


def ffmpeg_available() -> bool:
    """Whether a poster can be generated here at all. Used by the backfill."""
    return _ffmpeg() is not None


def extract_poster(data: bytes) -> bytes | None:
    """A JPEG of one frame of the video in ``data``, or None.

    The bytes are written to a temporary file because ffmpeg needs a seekable
    input for the fast `-ss` path, and a pipe is not. The temporary file is
    removed on every path out, including an exception.
    """
    binary = _ffmpeg()
    if binary is None:
        return None

    with tempfile.TemporaryDirectory(prefix="pulse-poster-") as workdir:
        source = Path(workdir) / "in"
        target = Path(workdir) / "out.jpg"
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
            # Seek before `-i` so ffmpeg jumps rather than decoding from the
            # start; on a long clip that is the difference between instant and
            # many seconds.
            "-ss",
            str(SEEK_SECONDS),
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            f"scale='min({POSTER_MAX_EDGE},iw)':-2",
            "-q:v",
            str(POSTER_QUALITY),
            "-f",
            "image2",
            str(target),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                timeout=EXTRACT_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            logger.info("Poster extraction failed: %s", exc)
            return None

        try:
            poster = target.read_bytes()
        except OSError:
            return None

    # A zero-byte file means ffmpeg exited 0 having written nothing, which would
    # otherwise be stored as a poster that never renders.
    return poster or None
