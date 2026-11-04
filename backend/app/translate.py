"""Language detection and translation for posts written in another language.

Two jobs, both server-side:

* Detect the language of a post so the UI can decide whether to offer a
  translation at all. Detection is a local library with no network cost, and it
  is deliberately conservative: a short post or a low-confidence guess is
  reported as "unknown" rather than guessed at, because a wrong "Translate"
  button on an English post is worse than no button.
* Translate the text on request. The service used is a free, keyless public
  endpoint, which is what keeps this feature working on a deployment that pays
  for nothing. It caps a request at a few hundred characters, so longer posts are
  split on sentence boundaries and translated in pieces.

Nothing here is imported by the app's startup path except lazily: a missing
`langdetect` or an unreachable translation service must degrade to "no translate
button", never to a failed request.
"""

import logging
import re
from functools import lru_cache
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger("pulse.translate")

# Languages written in a script that is unmistakable even for one or two words.
# Latin and Cyrillic are deliberately absent: `mk`/`bg`/`ru` and the Romance
# languages are all confidently confused for each other on short text, so a
# short post in those scripts is left alone rather than guessed at.
_CJK_AND_OTHER = {
    "ja": "Japanese",
    "zh-cn": "Chinese",
    "zh-tw": "Chinese",
    "ko": "Korean",
    "ar": "Arabic",
    "fa": "Persian",
    "ur": "Urdu",
    "he": "Hebrew",
    "el": "Greek",
    "th": "Thai",
    "hi": "Hindi",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "pa": "Punjabi",
    "si": "Sinhala",
    "ka": "Georgian",
    "hy": "Armenian",
    "am": "Amharic",
    "my": "Burmese",
    "km": "Khmer",
    "lo": "Lao",
    "ne": "Nepali",
}

# Human-readable names for the languages detection can return. Used to label the
# translate prompt ("Translate from French"), so it needs to cover what the
# detector actually produces rather than every ISO code in existence.
LANGUAGE_NAMES = {
    "af": "Afrikaans",
    "ar": "Arabic",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "kn": "Kannada",
    "ko": "Korean",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mr": "Marathi",
    "ne": "Nepali",
    "nl": "Dutch",
    "no": "Norwegian",
    "pa": "Punjabi",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "so": "Somali",
    "sq": "Albanian",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tl": "Tagalog",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "zh-cn": "Chinese",
    "zh-tw": "Chinese",
    **_CJK_AND_OTHER,
}

# Below this many characters a Latin-script guess is not trustworthy: "ok" and
# "nice one" are confidently reported as Slovak and Polish. A post that short is
# left alone rather than offered a translation of a language it may not be in.
_MIN_CONFIDENT_CHARS = 20

# Confidence the detector must reach before the language is reported. A guess
# under this is treated as unknown, which suppresses the translate prompt.
_MIN_CONFIDENCE = 0.75

# Split a long post into sentence-sized pieces for the translation service, which
# refuses anything past a few hundred characters per request.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？\n])\s+")


def language_name(code: str | None) -> str | None:
    """The display name for a language code, or None if it is not known."""
    if not code:
        return None
    return LANGUAGE_NAMES.get(code.lower())


@lru_cache(maxsize=512)
def detect_language(text: str) -> tuple[str | None, float]:
    """Detect the language of ``text`` as ``(code, confidence)``.

    Returns ``(None, 0.0)`` when the text is too short or the guess too weak to
    act on, and when the detector is unavailable. Cached because the same post
    body is asked about on every feed render.

    Never raises: a post whose language cannot be determined simply gets no
    translate prompt.
    """
    body = text.strip()
    if len(body) < 3:
        return (None, 0.0)

    try:
        from langdetect import DetectorFactory, LangDetectException, detect_langs
    except ImportError:  # pragma: no cover - only when the wheel is absent
        logger.warning("langdetect is not installed; post translation is disabled")
        return (None, 0.0)

    # The library is randomized unless the seed is pinned, which would make the
    # same post detect differently between requests.
    DetectorFactory.seed = 0

    try:
        results = detect_langs(body)
    except LangDetectException:
        return (None, 0.0)
    except Exception:  # noqa: BLE001 - detection is an enhancement
        logger.warning("language detection failed", exc_info=True)
        return (None, 0.0)

    if not results:
        return (None, 0.0)

    top = results[0]
    code = str(top.lang).lower()
    confidence = float(top.prob)

    # A short Latin-script string is guessed at with false confidence, so it is
    # only trusted when the script itself is distinctive.
    if len(body) < _MIN_CONFIDENT_CHARS and code not in _CJK_AND_OTHER:
        return (None, 0.0)
    if confidence < _MIN_CONFIDENCE:
        return (None, 0.0)

    return (code, confidence)


def _chunks(text: str, limit: int) -> list[str]:
    """Split ``text`` into pieces no longer than ``limit`` characters.

    Splits on sentence boundaries where possible so each piece is a coherent
    unit, and falls back to a hard split for a single sentence that is itself
    over the limit.
    """
    if len(text) <= limit:
        return [text]

    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            pieces.append(current)
        # A single sentence longer than the limit still has to be sent, so it is
        # cut on whitespace rather than mid-word where possible.
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        current = sentence
    if current:
        pieces.append(current)
    return [piece for piece in pieces if piece]


def _translate_chunk(
    client: httpx.Client, chunk: str, source: str | None, target: str
) -> str | None:
    """Translate one piece, or None if the service could not."""
    # The endpoint takes `source|target`; "Autodetect" lets it decide, which
    # avoids a second detection call and is what the service expects when the
    # source is not pinned.
    pair = f"{source or 'Autodetect'}|{target}"
    try:
        response = client.get(
            settings.translate_endpoint,
            params={"q": chunk, "langpair": pair},
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
    except Exception:  # noqa: BLE001 - translation is best-effort
        logger.warning("translation request failed", exc_info=True)
        return None

    if payload.get("responseStatus") not in (200, "200"):
        logger.info("translation service refused: %s", payload.get("responseDetails"))
        return None

    data = payload.get("responseData") or {}
    translated = data.get("translatedText")
    if not isinstance(translated, str) or not translated.strip():
        return None
    return translated


def translate_text(text: str, source: str | None = None) -> str | None:
    """Translate ``text`` into the configured target language.

    Returns None when translation is disabled, the text is empty, or any piece
    could not be translated — a partially translated post would be worse than
    none, so one failure discards the whole result.
    """
    if not settings.translate_enabled:
        return None
    body = text.strip()
    if not body:
        return None

    target = settings.translate_target_language
    if source and source.lower().startswith(target):
        return None

    pieces = _chunks(body, settings.translate_max_chars)
    translated: list[str] = []
    with httpx.Client(timeout=settings.translate_timeout_seconds) as client:
        for piece in pieces:
            result = _translate_chunk(client, piece, source, target)
            if result is None:
                return None
            translated.append(result)

    joined = " ".join(translated).strip()
    return joined or None
