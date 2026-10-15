import type { ReactNode } from 'react'
import { useInfiniteScroll } from '../lib/useInfiniteScroll'
import { FeedSkeleton, Spinner } from './UI'

/**
 * Wrap a list so it loads more as the user reaches the bottom.
 *
 * The sentinel is only rendered while more pages exist, so the observer
 * disconnects on its own once the feed is exhausted.
 */
export function InfiniteFeed({
  children,
  hasMore,
  loading,
  onLoadMore,
  emptyState,
  skeletonRows = 3,
}: {
  children: ReactNode
  hasMore: boolean
  loading: boolean
  onLoadMore: () => void
  emptyState?: ReactNode
  skeletonRows?: number
}) {
  const sentinel = useInfiniteScroll<HTMLDivElement>(onLoadMore, {
    enabled: hasMore && !loading,
  })

  const isEmpty = !children || (Array.isArray(children) && children.length === 0)
  if (isEmpty && !hasMore && !loading && emptyState) return <>{emptyState}</>

  return (
    <>
      {children}
      {hasMore && (
        <div ref={sentinel} className="feed-sentinel">
          {loading ? <FeedSkeleton rows={skeletonRows} /> : <Spinner />}
        </div>
      )}
      {!hasMore && !isEmpty && <p className="feed-end">You have reached the end</p>}
    </>
  )
}
