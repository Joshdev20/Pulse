import { VideoIcon, VideoOffIcon } from './Icons'
import { setAutoplayEnabled, useAutoplay } from '../lib/preferences'

/**
 * A one-click version of the autoplay switch, next to the theme toggle.
 *
 * The profile menu carries the labelled switch; this is the shortcut for the
 * moment a clip starts and the reader wants it to stop. Both read the same
 * store, so the two can never disagree.
 */
export function AutoplayToggle({ className = '' }: { className?: string }) {
  const autoplay = useAutoplay()

  return (
    <button
      type="button"
      className={`icon-btn autoplay-toggle ${className}`.trim()}
      onClick={() => setAutoplayEnabled(!autoplay)}
      aria-pressed={autoplay}
      title={autoplay ? 'Autoplay videos is on' : 'Autoplay videos is off'}
      aria-label={autoplay ? 'Turn autoplay videos off' : 'Turn autoplay videos on'}
    >
      {autoplay ? <VideoIcon size={18} /> : <VideoOffIcon size={18} />}
    </button>
  )
}
