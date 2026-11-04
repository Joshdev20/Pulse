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
    "ha": "Hausa",
    "hu": "Hungarian",
    "id": "Indonesian",
    "ig": "Igbo",
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
    "yo": "Yoruba",
    "zh-cn": "Chinese",
    "zh-tw": "Chinese",
    **_CJK_AND_OTHER,
}

# Languages the statistical detector cannot do at all, recognised instead by
# letters that belong to them and to nothing else in this app's traffic. Igbo,
# Yoruba and Hausa are all absent from `langdetect`'s model, which does not
# return "unknown" for them — it returns a confident wrong answer. Igbo in
# particular is reported as Vietnamese with probability 1.0, because the two
# orthographies share the dotted vowels ị, ọ and ụ, so neither the confidence
# guard nor the length guard catches it.
#
# Hausa and Yoruba carry a letter no other language here uses, so one of them is
# decisive on its own. Igbo does not: ị/ọ/ụ are Igbo *unless* the text also
# contains a Vietnamese-only letter (ă â đ ê ô ơ ư), which is what tells a
# Vietnamese post using the same vowels apart from an Igbo one.
_HAUSA_LETTERS = frozenset("ɓɗƙƴƁƊƘƳ")
_YORUBA_LETTERS = frozenset("ṣṢ")
_IGBO_LETTERS = frozenset("ịọụṅỊỌỤṄ")
_VIETNAMESE_LETTERS = frozenset("ăâđêôơưĂÂĐÊÔƠƯ")

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


def _script_language(body: str) -> str | None:
    """The language a distinctive letter proves, or None if no letter does.

    Only for languages the statistical detector gets wrong or does not know.
    Checked before it, because a confident wrong answer is worse than none: an
    Igbo post labelled Vietnamese both mislabels the post and pins the wrong
    source on the translation request, which makes the service echo the text
    back untranslated.
    """
    letters = set(body)
    if letters & _HAUSA_LETTERS:
        return "ha"
    if letters & _YORUBA_LETTERS:
        return "yo"
    if letters & _IGBO_LETTERS and not letters & _VIETNAMESE_LETTERS:
        return "ig"
    return None


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

    # A letter that belongs to exactly one language settles it outright, and for
    # Igbo, Yoruba and Hausa it is the only thing that can: the statistical
    # detector has no model for any of the three.
    script = _script_language(body)
    if script is not None:
        return (script, 1.0)

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
) -> tuple[str | None, str | None]:
    """Translate one piece as ``(translated_text, detected_source)``.

    The request is tried as ``Autodetect`` first, so the service's own answer
    governs and a wrong local guess cannot make it echo the text back. Pinning a
    source that came from a local guess is exactly what made an Igbo post come
    back untranslated: the detector said Vietnamese, the service was told
    "Vietnamese", and it returned the Igbo text almost unchanged.

    The free service refuses ``Autodetect`` for some callers, though — a
    deployment behind a shared egress address is turned away where a laptop is
    not — so a rejected autodetect falls back to the source the caller named.
    That is the path that always worked, and by now the source is usually right:
    the languages the local detector gets wrong are read from their own letters
    before this call. The label follows the service when it names a language, and
    the caller's source otherwise.

    Returns ``(None, None)`` when neither attempt could answer.
    """
    payload = _request_translation(client, chunk, f"Autodetect|{target}")
    if payload is None and source and source.lower() != "autodetect":
        payload = _request_translation(client, chunk, f"{source}|{target}")
    if payload is None:
        return (None, None)

    data = payload.get("responseData") or {}
    translated = data.get("translatedText")
    if not isinstance(translated, str) or not translated.strip():
        return (None, None)

    detected = data.get("detectedLanguage")
    detected = detected.lower() if isinstance(detected, str) and detected else None
    return (translated, detected or source)


def _request_translation(
    client: httpx.Client, chunk: str, pair: str
) -> dict[str, Any] | None:
    """One call to the translation service, or None if it did not answer."""
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
    return payload


def translate_with_detection(text: str, source: str | None = None) -> tuple[str | None, str | None]:
    """Translate ``text`` and report the language the service detected.

    Returns ``(translated, detected_source)``. Either may be None: no
    translation when the service failed or the post is already in the target
    language, and no detected source when the service did not say.

    The detected source is authoritative for labelling — the service sees the
    text the same way a reader does — while the local ``source`` is only used to
    skip a post already written in the target language.
    """
    if not settings.translate_enabled:
        return (None, None)
    body = text.strip()
    if not body:
        return (None, None)

    target = settings.translate_target_language
    if source and source.lower().startswith(target):
        return (None, None)

    pieces = _chunks(body, settings.translate_max_chars)
    translated: list[str] = []
    detected: str | None = None
    with httpx.Client(timeout=settings.translate_timeout_seconds) as client:
        for piece in pieces:
            result, piece_source = _translate_chunk(client, piece, source, target)
            if result is None:
                return (None, None)
            translated.append(result)
            detected = detected or piece_source

    joined = " ".join(translated).strip()
    if not joined:
        return (None, None)
    return (joined, detected)


def translate_text(text: str, source: str | None = None) -> str | None:
    """Translate ``text`` into the configured target language.

    Returns None when translation is disabled, the text is empty, or any piece
    could not be translated — a partially translated post would be worse than
    none, so one failure discards the whole result.
    """
    translated, _ = translate_with_detection(text, source)
    return translated
