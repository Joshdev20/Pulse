import { useMediaStatus } from '../lib/useMediaUrl'

/**
 * The profile header image.
 *
 * Covers are fetched through the same authenticated, reference-counted cache as
 * every other attachment, so this is a blob URL rather than a plain `src`. The
 * gradient underneath is always rendered: it is the fallback when there is no
 * cover, and it shows through while the image loads, so the banner never
 * collapses to an empty strip.
 */
export function ProfileBanner({ url, name }: { url: string | null; name: string }) {
  // No deferral here: the banner is at the top of the page, so it is always
  // wanted immediately and lazy loading would only delay it. A failed fetch
  // simply leaves the gradient in place, which is a valid banner on its own.
  const { asset } = useMediaStatus(url)

  return (
    <div className="profile-banner">
      {asset && (
        <img
          className="profile-banner-img loaded"
          src={asset.url}
          alt={`${name}'s cover photo`}
          decoding="async"
        />
      )}
    </div>
  )
}
