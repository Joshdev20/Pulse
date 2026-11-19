import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'
import * as queryCache from './queryCache'
import type { Page, Post } from './types'

type Loader = (page: number) => Promise<Page<Post>>

/** What a mounted feed hands to the cache, so a later mount can restore it. */
interface FeedSnapshot {
  posts: Post[]
  page: number
  hasMore: boolean
  total: number
}

/**
 * Paginated post list with append-on-load-more, plus local mutations so
 * edits, deletes and likes stay in sync without a full refetch.
 *
 * Pass a `cacheKey` to keep the loaded pages when the component unmounts, so
 * returning to the feed restores what was already read instead of fetching page
 * one again. Without it, opening a post and coming back discarded every page the
 * reader had scrolled through — and the scroll position with it.
 */
export function usePostFeed(loader: Loader, deps: unknown[] = [], cacheKey?: string) {
  const cached = cacheKey ? queryCache.read<FeedSnapshot>(cacheKey) : undefined

  const [posts, setPosts] = useState<Post[]>(() => cached?.posts ?? [])
  const [page, setPage] = useState(() => cached?.page ?? 1)
  const [hasMore, setHasMore] = useState(() => cached?.hasMore ?? false)
  const [total, setTotal] = useState(() => cached?.total ?? 0)
  // A restored list paints immediately; only a cold start needs the skeleton.
  const [loading, setLoading] = useState(() => cached === undefined)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Guards against a stale response overwriting a newer one.
  const requestId = useRef(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  // Mirror the list into the cache on every change so a later mount restores the
  // latest state, including edits and deletions made while it was open.
  const snapshot = useRef({ posts, page, hasMore, total })
  snapshot.current = { posts, page, hasMore, total }
  useEffect(() => {
    if (cacheKey) queryCache.write(cacheKey, snapshot.current)
  }, [cacheKey, posts, page, hasMore, total])

  const load = useCallback(async (targetPage: number, append: boolean) => {
    const id = ++requestId.current
    if (append) setLoadingMore(true)
    else setLoading(true)
    setError(null)
    try {
      const result = await loaderRef.current(targetPage)
      if (id !== requestId.current) return
      setPosts((current) => (append ? [...current, ...result.items] : result.items))
      setHasMore(result.has_more)
      setTotal(result.total)
      setPage(result.page)
    } catch (caught) {
      if (id !== requestId.current) return
      // Keep showing the restored list when a background refresh fails; only a
      // cold load has nothing to fall back to.
      setError(caught instanceof ApiError ? caught.message : 'Could not load posts')
    } finally {
      if (id === requestId.current) {
        setLoading(false)
        setLoadingMore(false)
      }
    }
  }, [])

  // Fetch the first page only when there is nothing to show. A restored feed is
  // left alone: the reader came back to it, not to a refresh, and a silent
  // re-fetch would reorder the list under their scroll position.
  const hydrated = useRef(cached !== undefined)
  useEffect(() => {
    if (hydrated.current) {
      hydrated.current = false
      return
    }
    void load(1, false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  const loadMore = useCallback(() => {
    if (loadingMore || loading || !hasMore) return
    void load(page + 1, true)
  }, [hasMore, load, loading, loadingMore, page])

  const replace = useCallback((updated: Post) => {
    setPosts((current) => current.map((post) => (post.id === updated.id ? updated : post)))
  }, [])

  const remove = useCallback((id: number) => {
    setPosts((current) => current.filter((post) => post.id !== id))
    setTotal((value) => Math.max(0, value - 1))
  }, [])

  const prepend = useCallback((post: Post) => {
    setPosts((current) => [post, ...current])
    setTotal((value) => value + 1)
  }, [])

  const reload = useCallback(() => load(1, false), [load])

  return {
    posts,
    setPosts,
    total,
    hasMore,
    loading,
    loadingMore,
    error,
    loadMore,
    reload,
    replace,
    remove,
    prepend,
  }
}
