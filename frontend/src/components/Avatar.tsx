import { initials } from '../lib/format'
import { useInView } from '../lib/useInView'
import { useMediaUrl } from '../lib/useMediaUrl'
import { VerifiedIcon } from './Icons'

const SIZES = { xs: 24, sm: 32, md: 42, lg: 52, xl: 84 } as const

type AvatarSize = keyof typeof SIZES

export function Avatar({
  name,
  color,
  url,
  size = 'md',
  title,
  eager = false,
}: {
  name: string
  color: string
  /** Path to an uploaded profile picture; falls back to coloured initials. */
  url?: string | null
  size?: AvatarSize
  title?: string
  /** Skip lazy loading — for an avatar that is on screen at first paint. */
  eager?: boolean
}) {
  const pixels = SIZES[size] ?? SIZES.md
  const { ref, inView } = useInView<HTMLDivElement>({ rootMargin: '250px' })
  const shouldLoad = eager || inView
  // The fetch is what is deferred; `loading="lazy"` on the <img> would do
  // nothing here because the src is a blob URL that only exists after it.
  const asset = useMediaUrl(shouldLoad ? url : null)

  return (
    <div
      ref={ref}
      className="avatar"
      title={title ?? name}
      style={{
        width: pixels,
        height: pixels,
        background: `linear-gradient(140deg, ${color}, ${shade(color, -22)})`,
        fontSize: Math.round(pixels * (size === 'xs' || size === 'sm' ? 0.4 : 0.36)),
      }}
      aria-hidden="true"
    >
      {initials(name)}
      {asset && (
        <img
          className="avatar-img"
          src={asset.url}
          alt=""
          decoding="async"
          onLoad={(event) => event.currentTarget.classList.add('loaded')}
        />
      )}
    </div>
  )
}

/** Display name with the verified seal when the account carries one. */
export function DisplayName({
  user,
  className = '',
}: {
  user: { display_name: string; is_verified?: boolean }
  className?: string
}) {
  return (
    <span className={`display-name ${className}`.trim()}>
      {user.display_name}
      {user.is_verified && (
        <span className="verified-badge" title="Verified account" aria-label="Verified account">
          <VerifiedIcon size={15} />
        </span>
      )}
    </span>
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
