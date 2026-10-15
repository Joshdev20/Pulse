import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'pulse.theme'

/**
 * Resolve the starting theme.
 *
 * A saved choice always wins. Otherwise the operating system preference is
 * used, so a first visit matches the rest of the user's desktop.
 */
function initialTheme(): Theme {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'light' || saved === 'dark') return saved
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function apply(theme: Theme) {
  document.documentElement.dataset.theme = theme
  // Keeps form controls, scrollbars and the like in step with the palette.
  document.documentElement.style.colorScheme = theme
}

/**
 * Light/dark theme, persisted and applied to `<html data-theme>`.
 *
 * The attribute is set by an inline script in index.html before first paint, so
 * there is no flash of the wrong theme; this hook adopts that same value.
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => initialTheme())

  useEffect(() => {
    apply(theme)
    localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  const toggle = useCallback(() => {
    setTheme((current) => (current === 'dark' ? 'light' : 'dark'))
  }, [])

  return { theme, setTheme, toggle }
}

/** Read the theme without subscribing, for code that only needs the value once. */
export function currentTheme(): Theme {
  const attribute = document.documentElement.dataset.theme
  return attribute === 'light' ? 'light' : 'dark'
}
