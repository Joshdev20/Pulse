import { initials } from '../lib/format'

const SIZES: Record<string, number> = { sm: 32, md: 42, lg: 52, xl: 84 }

export function Avatar({
  name,
  color,
  size = 'md',
  title,
}: {
  name: string
  color: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
  title?: string
}) {
  const pixels = SIZES[size] ?? SIZES.md
  return (
    <div
      className="avatar"
      title={title ?? name}
      style={{
        width: pixels,
        height: pixels,
        background: `linear-gradient(140deg, ${color}, ${shade(color, -22)})`,
        fontSize: Math.round(pixels * (size === 'sm' ? 0.4 : 0.36)),
      }}
      aria-hidden="true"
    >
      {initials(name)}
    </div>
  )
}

/** Darken/lighten a hex colour by a percentage. */
function shade(hex: string, percent: number): string {
  const value = hex.replace('#', '')
  if (value.length !== 6) return hex
  const num = parseInt(value, 16)
  const clamp = (channel: number) => Math.max(0, Math.min(255, channel))
  const r = clamp(((num >> 16) & 255) + Math.round(2.55 * percent))
  const g = clamp(((num >> 8) & 255) + Math.round(2.55 * percent))
  const b = clamp((num & 255) + Math.round(2.55 * percent))
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, '0')}`
}
