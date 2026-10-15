import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'
import type { Page, Post } from './types'

type Loader = (page: number) => Promise<Page<Post>>

/**
 * Paginated post list with append-on-load-more, plus local mutations so
 * edits, deletes and likes stay in sync without a full refetch.
 */
export function usePostFeed(loader: Loader, deps: unknown[] = []) {
  const [posts, setPosts] = useState<Post[]>([])
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Guards against a stale response overwriting a newer one.
  const requestId = useRef(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

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
      setError(caught instanceof ApiError ? caught.message : 'Could not load posts')
    } finally {
      if (id === requestId.current) {
        setLoading(false)
        setLoadingMore(false)
      }
    }
  }, [])

  useEffect(() => {
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
