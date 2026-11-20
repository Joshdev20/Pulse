#!/usr/bin/env python3
"""Regenerate `frontend/src/lib/stickers.ts` from the server catalogue.

`backend/app/stickers.py` is the single source of truth: the send route validates
ids against it and `GET /api/conversations/stickers` publishes it. The frontend
still needs the emoji at render time, so it carries a copy — and a hand-kept copy
drifts. It has drifted once already, which showed a literal `U0001f389 Sticker`
in the inbox instead of the emoji.

Run from the repository root after editing the catalogue:

    python3 scripts/gen_stickers.py

The output is deterministic, so a clean run leaves the file byte-identical.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.stickers import STICKERS  # noqa: E402

TARGET = ROOT / "frontend" / "src" / "lib" / "stickers.ts"

HEADER = """import type { Message } from './types'
import { callPreview } from './calls-format'

/* Generated from `backend/app/stickers.py`. Edit the catalogue there and
   re-run the generator (`python3 scripts/gen_stickers.py`); do not hand-edit
   this map. */

export const STICKER_EMOJI: Record<string, { emoji: string; label: string }> = {
"""

FOOTER = """}

/** The one-line text an inbox row shows for a message. */
export function previewOf(message: Message): string {
  // A call has no body, so it is named first: falling through would show an
  // empty row for the one entry where the wording carries the whole message.
  if (message.call) return callPreview(message.call)
  if (message.sticker) {
    const entry = STICKER_EMOJI[message.sticker]
    return entry ? `${entry.emoji} Sticker` : 'Sticker'
  }
  if (message.body) return message.body
  return message.media.length > 0 ? 'Attachment' : ''
}
"""


def render() -> str:
    lines = [HEADER]
    for sticker in STICKERS:
        # The emoji stays a literal rather than an escape: it has to read as an
        # emoji in an editor and in a diff, which is how the drift was spotted.
        label = sticker.label.replace("\\", "\\\\").replace("'", "\\'")
        # The key is quoted because an id such as `flag-ng` is not a valid
        # JavaScript identifier and would not parse as a bare key.
        lines.append(f"  '{sticker.id}': {{ emoji: '{sticker.emoji}', label: '{label}' }},\n")
    lines.append(FOOTER)
    return "".join(lines)


def main() -> int:
    rendered = render()
    if TARGET.exists() and TARGET.read_text(encoding="utf-8") == rendered:
        print(f"{TARGET.relative_to(ROOT)} is already up to date")
        return 0
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(STICKERS)} stickers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
