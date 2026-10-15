import { useEffect, useState } from 'react'

/**
 * A clock shared by every relative timestamp on screen.
 *
 * One interval drives all of them rather than one timer per timestamp, and it
 * ticks faster while something is very recent so a just-created post flips from
 * "now" to "1m" promptly without re-rendering the feed every second forever.
 */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])

  return now
}
