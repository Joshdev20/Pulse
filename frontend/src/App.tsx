import { useCallback, useState } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { AppShell } from './components/Layout'
import { Composer } from './components/Composer'
import { NotificationCenter } from './components/NotificationCenter'
import { Modal, Spinner } from './components/UI'
import { useAuth } from './context/AuthContext'
import { RealtimeProvider } from './context/RealtimeContext'
import { HomePage } from './pages/HomePage'
import { ExplorePage } from './pages/ExplorePage'
import { AdminPage } from './pages/AdminPage'
import { ForgotPasswordPage } from './pages/ForgotPasswordPage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { ResetPasswordPage } from './pages/ResetPasswordPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { PostDetailPage } from './pages/PostDetailPage'
import { ProfilePage } from './pages/ProfilePage'
import { SearchPage } from './pages/SearchPage'
import { SettingsPage } from './pages/SettingsPage'
import { MessagesPage } from './pages/MessagesPage'
import { NotFoundPage } from './pages/NotFoundPage'
import { usePageTitle } from './lib/usePageTitle'

/** Routes with room for the right-hand rail on wide screens. */
const RAIL_ROUTES = ['/', '/explore', '/search', '/notifications']

function FullPageSpinner() {
  return (
    <div className="state">
      <Spinner size={22} />
    </div>
  )
}

/**
 * Gate the whole application behind a session.
 *
 * The feed is no longer public: an unauthenticated visitor is redirected to
 * sign in and returned to the page they asked for afterwards.
 */
function RequireAuth() {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) return <FullPageSpinner />
  if (!user) {
    return (
      <Navigate to="/login" state={{ from: `${location.pathname}${location.search}` }} replace />
    )
  }
  return <Outlet />
}

/** Signed-in users have no reason to see the login or register screens. */
function RedirectIfAuthed({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  // `?add=1` is the account switcher asking to sign in as somebody else. The
  // current session must stay put while that happens, so the redirect that
  // would normally bounce a signed-in visitor away is skipped here.
  const adding = new URLSearchParams(location.search).get('add') === '1'
  if (loading) return <FullPageSpinner />
  if (user && !adding) return <Navigate to="/" replace />
  return <>{children}</>
}

function Layout() {
  const [composeOpen, setComposeOpen] = useState(false)
  const { pathname } = useLocation()

  usePageTitle(pathname)

  const openComposer = useCallback(() => setComposeOpen(true), [])

  return (
    <>
      <AppShell withRail={RAIL_ROUTES.includes(pathname)} onCompose={openComposer}>
        <Outlet />
      </AppShell>
      {/* Rendered inside the authenticated shell so popups and the chime only
          run while a session is active, even when the tab is in the background. */}
      <NotificationCenter />
      {composeOpen && (
        <Modal title="New post" onClose={() => setComposeOpen(false)}>
          <Composer autoFocus onPosted={() => setComposeOpen(false)} />
        </Modal>
      )}
    </>
  )
}

function ComposerPage() {
  const navigate = useNavigate()
  return (
    <Modal title="New post" onClose={() => navigate(-1)}>
      <Composer autoFocus onPosted={() => navigate('/')} />
    </Modal>
  )
}

export function App() {
  // AuthProvider lives in main.tsx, above the router. Mounting it here too
  // would run a second, unused session check and double every auth request.
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <RedirectIfAuthed>
            <LoginPage />
          </RedirectIfAuthed>
        }
      />
      <Route
        path="/register"
        element={
          <RedirectIfAuthed>
            <RegisterPage />
          </RedirectIfAuthed>
        }
      />
      <Route
        path="/forgot-password"
        element={
          <RedirectIfAuthed>
            <ForgotPasswordPage />
          </RedirectIfAuthed>
        }
      />
      {/* Not wrapped in RedirectIfAuthed: the reset itself signs the user in,
          and a signed-in visitor may still be following an emailed link. */}
      <Route path="/reset-password" element={<ResetPasswordPage />} />

      {/* Everything below requires a session. */}
      <Route element={<RequireAuth />}>
        <Route
          element={
            <RealtimeProvider>
              <Layout />
            </RealtimeProvider>
          }
        >
          <Route path="/" element={<HomePage />} />
          <Route path="/explore" element={<ExplorePage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/p/:postId" element={<PostDetailPage />} />
          <Route path="/u/:username" element={<ProfilePage />} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="/messages" element={<MessagesPage />} />
          <Route path="/messages/:conversationId" element={<MessagesPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/compose" element={<ComposerPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  )
}
