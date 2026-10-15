import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import type { User } from '../lib/types'

export function FollowButton({
  user,
  onChange,
  size = 'md',
}: {
  user: User
  onChange?: (user: User) => void
  size?: 'sm' | 'md'
}) {
  const { user: me } = useAuth()
  const { notify } = useToast()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [hover, setHover] = useState(false)

  if (me?.id === user.id) return null

  async function toggle(event: React.MouseEvent) {
    event.stopPropagation()
    event.preventDefault()
    if (!me) {
      navigate('/login')
      return
    }
    if (busy) return
    setBusy(true)
    try {
      const updated = user.is_following ? await api.unfollow(user.username) : await api.follow(user.username)
      onChange?.(updated)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Could not update follow', 'error')
    } finally {
      setBusy(false)
    }
  }

  const following = user.is_following
  const label = following ? (hover ? 'Unfollow' : 'Following') : 'Follow'

  return (
    <button
      type="button"
      className={`btn ${size === 'sm' ? 'btn-sm ' : ''}${
        following ? 'btn-following' : 'btn-primary'
      }`}
      disabled={busy}
      onClick={toggle}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-pressed={following}
    >
      {label}
    </button>
  )
}
