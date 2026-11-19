import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
import { AuthProvider } from './context/AuthContext'
import { ToastProvider } from './context/ToastContext'
import './styles.css'

const container = document.getElementById('root')
if (!container) throw new Error('Root element not found')

// Register the service worker on every load, not only when push is turned on.
// Chrome's install criteria call for a registered worker, so a user who never
// enabled push had no worker at all and the site was not installable. The push
// code still registers it itself, because it needs the result; registration is
// idempotent, so the two do not conflict. Deferred to `load` so it never
// competes with the first render.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker
      .register('/sw.js', { updateViaCache: 'none' })
      .catch(() => undefined)
  })
}

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  </StrictMode>,
)

// Drop the document-level splash once React owns the screen. `App` renders its
// own BrandLoader in the same spot, so removing this only hands the animation
// over; without it the splash would sit on top of the app forever.
const splash = document.querySelector('.boot-splash')
if (splash) {
  // Deferred a frame so React's first commit is already painted underneath,
  // which keeps the handover from flashing the page background.
  requestAnimationFrame(() => splash.remove())
}
