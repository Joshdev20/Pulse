import { Link } from 'react-router-dom'
import { Avatar, DisplayName } from './Avatar'
import { FollowButton } from './FollowButton'
import type { User } from '../lib/types'

export function UserRow({
  user,
  onChange,
  showBio = true,
}: {
  user: User
  onChange?: (user: User) => void
  showBio?: boolean
}) {
  return (
    <div className="user-row">
      <Link to={`/u/${user.username}`}>
        <Avatar name={user.display_name} color={user.avatar_color} url={user.avatar_url} size="md" />
      </Link>
      <div className="user-row-main">
        <div className="user-row-name">
          <Link to={`/u/${user.username}`}>
            <DisplayName user={user} />
          </Link>
        </div>
        <div className="user-row-handle">@{user.username}</div>
        {showBio && user.bio && <p className="user-row-bio">{user.bio}</p>}
      </div>
      <div className="user-row-actions">
        <FollowButton user={user} onChange={onChange} size="sm" />
      </div>
    </div>
  )
}
