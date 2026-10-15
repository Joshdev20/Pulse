import { Link } from 'react-router-dom'
import { EmptyState } from '../components/UI'
import { SearchIcon } from '../components/Icons'

export function NotFoundPage() {
  return (
    <EmptyState
      icon={<SearchIcon size={24} />}
      title="Page not found"
      description="The page you were looking for does not exist or may have been removed."
      action={
        <Link to="/" className="btn btn-primary btn-sm">
          Back to home
        </Link>
      }
    />
  )
}
