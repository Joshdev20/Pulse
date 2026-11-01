/**
 * The Pulse service worker.
 *
 * Its single job is Web Push: receiving a push delivered by the browser's push
 * service and showing it as a notification. That is what makes an alert arrive
 * with Pulse closed — an in-page popup needs a visible tab and the OS
 * notification API needs an open one, but a service worker is woken by the
 * browser itself.
 *
 * Deliberately not a caching worker. Pulse is a live app whose data is
 * per-request and authenticated; a cache in front of it would serve one user's
 * feed to another on a shared device. The only caching here is none at all.
 *
 * Plain JavaScript, not TypeScript: this file is served verbatim from
 * `public/` and is never bundled, so it cannot import from `src/`.
 */

/* eslint-env serviceworker */

self.addEventListener('install', () => {
  // Take over as soon as possible so the first push is not missed.
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim())
})

/**
 * A pass-through fetch handler, present purely to satisfy the install criteria.
 *
 * Chrome refuses to fire `beforeinstallprompt` unless the service worker has a
 * fetch listener, even though it dropped that requirement for the menu entry in
 * 108. Without one, Android users never get the in-app install prompt and are
 * left to find "Add to Home screen" themselves.
 *
 * Deliberately does *not* call `respondWith`: an empty listener leaves the
 * browser to make the request exactly as it would without a worker, so nothing
 * is cached and no response is synthesised. That keeps the promise above — a
 * cache here would serve one signed-in user's feed to the next on a shared
 * device. Adding `respondWith(fetch(...))` would also work but would take the
 * request over for no benefit and risk a rejected promise turning into a failed
 * navigation.
 */
self.addEventListener('fetch', () => {
  // Intentionally empty; see the comment above.
})

self.addEventListener('push', (event) => {
  let payload = {}
  try {
    payload = event.data ? event.data.json() : {}
  } catch {
    // A push with no body, or one that is not JSON, still deserves an alert:
    // some browsers send a bare wake-up ping.
    payload = {}
  }

  const title = payload.title || 'Pulse'
  const body = payload.body || 'You have a new notification.'
  const url = payload.url || '/notifications'

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      // Same tag replaces the previous alert for the same conversation or
      // notification, so a burst collapses instead of stacking up.
      tag: payload.tag,
      renotify: Boolean(payload.tag),
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      data: { url },
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = event.notification.data?.url || '/notifications'

  // Focus a Pulse tab if one is already open, rather than opening a second.
  // Navigating an existing client keeps the session and the realtime socket.
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (new URL(client.url).origin === self.location.origin) {
          client.navigate(target)
          return client.focus()
        }
      }
      return self.clients.openWindow(target)
    }),
  )
})
