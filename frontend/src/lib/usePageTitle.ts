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
 * Keep `document.title` in step with the active route.
 *
 * Login and register set their own titles while mounted; every signed-in page
 * goes through here so the tab never keeps a stale label after navigation.
 */
export function usePageTitle(pathname: string, suffix?: string) {
  useEffect(() => {
    const key = `/${pathname.split('/')[1] ?? ''}`
    const label = suffix ?? ROUTE_TITLES[key] ?? 'Pulse'
    document.title = label === BASE_TITLE ? BASE_TITLE : `${label} · ${BASE_TITLE}`
  }, [pathname, suffix])
}
