import type { Message } from './types'

/* Generated from `backend/app/stickers.py`. Edit the catalogue there and
   re-run the generator (`python3 scripts/gen_stickers.py`); do not hand-edit
   this map. */

export const STICKER_EMOJI: Record<string, { emoji: string; label: string }> = {
  heart: { emoji: '❤️', label: 'Love' },
  laugh: { emoji: '😂', label: 'Laughing' },
  wow: { emoji: '😮', label: 'Wow' },
  sad: { emoji: '😢', label: 'Sad' },
  angry: { emoji: '😠', label: 'Annoyed' },
  fire: { emoji: '🔥', label: 'On fire' },
  party: { emoji: '🎉', label: 'Celebrate' },
  clap: { emoji: '👏', label: 'Applause' },
  thumbs: { emoji: '👍', label: 'Nice' },
  pray: { emoji: '🙏', label: 'Thanks' },
  wave: { emoji: '👋', label: 'Hello' },
  hug: { emoji: '🤗', label: 'Hug' },
  kiss: { emoji: '😘', label: 'Kiss' },
  wink: { emoji: '😉', label: 'Wink' },
  cool: { emoji: '😎', label: 'Cool' },
  sleep: { emoji: '😴', label: 'Sleepy' },
  think: { emoji: '🤔', label: 'Thinking' },
  shrug: { emoji: '🤷', label: 'Shrug' },
  eyes: { emoji: '👀', label: 'Watching' },
  salute: { emoji: '🫡', label: 'Salute' },
  coffee: { emoji: '☕', label: 'Coffee' },
  pizza: { emoji: '🍕', label: 'Pizza' },
  cake: { emoji: '🎂', label: 'Cake' },
  gift: { emoji: '🎁', label: 'Gift' },
  money: { emoji: '💸', label: 'Money' },
  rocket: { emoji: '🚀', label: 'Rocket' },
  star: { emoji: '⭐', label: 'Star' },
  sun: { emoji: '☀️', label: 'Sunny' },
  rain: { emoji: '🌧️', label: 'Raining' },
  moon: { emoji: '🌙', label: 'Goodnight' },
  check: { emoji: '✅', label: 'Done' },
  cross: { emoji: '❌', label: 'No' },
  clock: { emoji: '⏰', label: 'Running late' },
  phone: { emoji: '📞', label: 'Call me' },
  mail: { emoji: '📩', label: 'Message me' },
  pin: { emoji: '📍', label: 'Right here' },
  lock: { emoji: '🔒', label: 'Private' },
  key: { emoji: '🔑', label: 'Access' },
  bulb: { emoji: '💡', label: 'Idea' },
  muscle: { emoji: '💪', label: 'Strong' },
}

/** The one-line text an inbox row shows for a message. */
export function previewOf(message: Message): string {
  if (message.sticker) {
    const entry = STICKER_EMOJI[message.sticker]
    return entry ? `${entry.emoji} Sticker` : 'Sticker'
  }
  if (message.body) return message.body
  return message.media.length > 0 ? 'Attachment' : ''
}
