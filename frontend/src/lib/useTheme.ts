import { useCallback, useEffect, useSyncExternalStore } from 'react'

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
 * The theme lives outside React, not in a component's state.
 *
 * The switch appears in more than one place — the sidebar and Settings — and a
 * `useState` per component would let the two drift apart: flipping one would
 * leave the other rendering the old icon. A single external value read through
 * `useSyncExternalStore` keeps every subscriber on the same answer.
 */
let current: Theme = initialTheme()
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function getSnapshot(): Theme {
  return current
}

export function setTheme(next: Theme) {
  if (next === current) return
  current = next
  apply(next)
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // Private browsing can refuse writes; the theme still applies this session.
  }
  emit()
}

export function toggleTheme() {
  setTheme(current === 'dark' ? 'light' : 'dark')
}

function savedTheme(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

/**
 * Follow the operating system while the user has made no explicit choice.
 *
 * Without this a tab left open overnight keeps the palette it started with
 * after the desktop switches to night mode.
 */
if (typeof window !== 'undefined' && window.matchMedia) {
  window.matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', (event) => {
    const saved = savedTheme()
    if (saved === 'light' || saved === 'dark') return
    setTheme(event.matches ? 'light' : 'dark')
  })
}

/** Light/dark theme, persisted and applied to `<html data-theme>`. */
export function useTheme() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)

  // Adopt whatever the inline script in index.html already painted, so the
  // store and the document agree even if they resolved the theme differently.
  useEffect(() => {
    apply(theme)
  }, [theme])

  return {
    theme,
    setTheme: useCallback((next: Theme) => setTheme(next), []),
    toggle: useCallback(() => toggleTheme(), []),
  }
}

/** Read the theme without subscribing, for code that only needs the value once. */
export function currentTheme(): Theme {
  return current
}
