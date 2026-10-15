import { MoonIcon, SunIcon } from './Icons'
import { useTheme } from '../lib/useTheme'

/**
 * Light/dark switch.
 *
 * Renders as a single button whose label describes the theme it will switch
 * *to*, which is what a screen reader user needs to hear.
 */
export function ThemeToggle({ className = '' }: { className?: string }) {
  const { theme, toggle } = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'

  return (
    <button
      type="button"
      className={`icon-btn theme-toggle ${className}`.trim()}
      onClick={toggle}
      title={`Switch to ${next} mode`}
      aria-label={`Switch to ${next} mode`}
    >
      {theme === 'dark' ? <SunIcon size={18} /> : <MoonIcon size={18} />}
    </button>
  )
}
