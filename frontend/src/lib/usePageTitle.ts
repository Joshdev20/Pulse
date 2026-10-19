import { useEffect } from 'react'

const BASE_TITLE = 'Pulse'

/** Titles for the signed-in routes, keyed by the path's leading segment. */
const ROUTE_TITLES: Record<string, string> = {
  '/': 'Home',
  '/explore': 'Explore',
  '/search': 'Search',
  '/notifications': 'Notifications',
  '/messages': 'Messages',
  '/settings': 'Edit profile',
  '/compose': 'New post',
  '/admin': 'Admin',
}

/**
 * Routes whose page component sets a title from loaded data.
 *
 * The shell must not overwrite those. React runs a child's effects before its
 * parent's, so the shell's generic label would otherwise land last and replace
 * "Ada Lovelace (@ada)" with a bare "Pulse" — which is also what a search
 * engine would show for the page.
 */
export const DYNAMIC_TITLE_ROUTES = new Set(['/u'])

/** The route segment a path belongs to, as used to key {@link ROUTE_TITLES}. */
export function titleRouteKey(pathname: string): string {
  return `/${pathname.split('/')[1] ?? ''}`
}

/**
 * Keep `document.title` in step with the active route.
 *
 * Login and register set their own titles while mounted; every signed-in page
 * goes through here so the tab never keeps a stale label after navigation.
 *
 * `enabled` is false when the page owns its title (see
 * {@link DYNAMIC_TITLE_ROUTES}); it is a parameter rather than a check inside
 * the hook so the page that owns the title can still call it with `enabled`
 * left true.
 */
export function usePageTitle(pathname: string, suffix?: string, enabled = true) {
  useEffect(() => {
    if (!enabled) return
    const label = suffix ?? ROUTE_TITLES[titleRouteKey(pathname)] ?? 'Pulse'
    document.title = label === BASE_TITLE ? BASE_TITLE : `${label} · ${BASE_TITLE}`
  }, [pathname, suffix, enabled])
}

/**
 * Set the page's meta description while a component is mounted.
 *
 * A search engine will often take the description from the visible text, but a
 * profile with a thin bio benefits from an explicit one. The previous value is
 * restored on unmount so navigating back to a route that does not set one does
 * not leave this page's text behind.
 */
export function useMetaDescription(content: string | undefined) {
  useEffect(() => {
    if (!content) return
    const tag = document.querySelector('meta[name="description"]')
    if (!tag) return
    const previous = tag.getAttribute('content')
    tag.setAttribute('content', content)
    return () => {
      if (previous !== null) tag.setAttribute('content', previous)
    }
  }, [content])
}
