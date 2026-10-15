import { useCallback, useState } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { AppShell } from './components/Layout'
import { Composer } from './components/Composer'
import { Modal, Spinner } from './components/UI'
import { useAuth } from './context/AuthContext'
import { HomePage } from './pages/HomePage'
import { ExplorePage } from './pages/ExplorePage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { PostDetailPage } from './pages/PostDetailPage'
import { ProfilePage } from './pages/ProfilePage'
import { SearchPage } from './pages/SearchPage'
import { SettingsPage } from './pages/SettingsPage'
import { NotFoundPage } from './pages/NotFoundPage'

/** Routes with room for the right-hand rail on wide screens. */
const RAIL_ROUTES = ['/', '/explore', '/search', '/notifications']

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="state">
        <Spinner size={22} />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" state={{ from: location.pathname }} replace />
  return <>{children}</>
}

function Layout() {
  const [composeOpen, setComposeOpen] = useState(false)
  const { user } = useAuth()
  const { pathname } = useLocation()
  const navigate = useNavigate()

  const openComposer = useCallback(() => {
    if (!user) {
      navigate('/login')
      return
    }
    setComposeOpen(true)
  }, [navigate, user])

  return (
    <>
      <AppShell withRail={RAIL_ROUTES.includes(pathname)} onCompose={openComposer}>
        <Outlet />
      </AppShell>
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
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/explore" element={<ExplorePage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/p/:postId" element={<PostDetailPage />} />
        <Route path="/u/:username" element={<ProfilePage />} />
        <Route
          path="/notifications"
          element={
            <RequireAuth>
              <NotificationsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/settings"
          element={
            <RequireAuth>
              <SettingsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/compose"
          element={
            <RequireAuth>
              <ComposerPage />
            </RequireAuth>
          }
        />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
