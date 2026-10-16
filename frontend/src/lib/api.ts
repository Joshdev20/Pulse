import type {
  AdminComment,
  AdminPost,
  AdminStats,
  AdminUser,
  AdminUserStatus,
  Comment,
  Conversation,
  Media,
  Message,
  Notification,
  Page,
  Post,
  Stats,
  SuggestedUser,
  TrendingTag,
  User,
} from './types'

const TOKEN_KEY = 'pulse.token'

/**
 * Base URL of the API, without a trailing slash.
 *
 * Empty in local dev, where Vite proxies `/api` to the backend so requests stay
 * same-origin. In a split deploy the frontend and API live on different hosts,
 * so this is set at build time (e.g. `https://pulse-api.onrender.com`) and every
 * request becomes absolute.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')

/** Make an API path absolute when a base URL is configured. */
export function apiUrl(path: string): string {
  return API_BASE ? `${API_BASE}${path}` : path
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

/** Extract a readable message from FastAPI's error shapes. */
function readError(payload: unknown, fallback: string): string {
  if (typeof payload === 'string' && payload) return payload
  if (payload && typeof payload === 'object') {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string; loc?: string[] }
      if (first?.msg) {
        const field = first.loc?.filter((part) => part !== 'body').join('.')
        return field ? `${field}: ${first.msg}` : first.msg
      }
    }
  }
  return fallback
}

/**
 * Message for a request that the web server's SPA fallback answered with
 * index.html instead of the backend.
 *
 * This is what an unset `VITE_API_BASE` looks like in a split deploy: the site
 * renders and looks healthy, but every API call comes back as HTML. Without
 * this check the caller sees a baffling JSON parse error instead.
 */
function htmlFallbackMessage(subject: string): string {
  return API_BASE
    ? `${API_BASE} returned HTML instead of JSON. Check that VITE_API_BASE points at the backend.`
    : `The API is not configured for this deployment: ${subject} was answered by the web server instead of the backend. Set VITE_API_BASE to the backend origin and redeploy.`
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(apiUrl(path), { ...options, headers })

  // When the API base is unset in a split deploy, the web server answers
  // /api/* from its SPA fallback and returns index.html with a 200 or 405.
  // Without this check that surfaces as a baffling JSON parse error.
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('text/html')) {
    throw new ApiError(response.status, htmlFallbackMessage('the request'))
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const payload = text ? safeParse(text) : null

  if (!response.ok) {
    throw new ApiError(response.status, readError(payload, response.statusText || 'Request failed'))
  }
  return payload as T
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export const api = {
  register: (data: { username: string; email: string; display_name: string; password: string }) =>
    request<{ access_token: string }>('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  login: (username: string, password: string) =>
    request<{ access_token: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),

  me: () => request<User>('/api/auth/me'),

  forgotPassword: (email: string) =>
    request<{ detail: string; reset_token: string | null; reset_url: string | null }>(
      '/api/auth/forgot-password',
      { method: 'POST', body: JSON.stringify({ email }) },
    ),

  resetPassword: (token: string, password: string) =>
    request<{ access_token: string }>('/api/auth/reset-password', {
      method: 'POST',
      body: JSON.stringify({ token, password }),
    }),

  updateProfile: (
    username: string,
    data: Partial<
      Pick<User, 'display_name' | 'bio' | 'location' | 'website' | 'avatar_color'> & {
        avatar_media_id: number | null
        cover_media_id: number | null
      }
    >,
  ) => request<User>(`/api/users/${username}`, { method: 'PATCH', body: JSON.stringify(data) }),

  uploadAvatar: (file: File, onProgress?: (percent: number) => void) =>
    uploadWithProgress<User>('/api/users/me/avatar', file, '', onProgress),

  clearAvatar: () => request<User>('/api/users/me/avatar', { method: 'DELETE' }),

  uploadCover: (file: File, onProgress?: (percent: number) => void) =>
    uploadWithProgress<User>('/api/users/me/cover', file, '', onProgress),

  clearCover: () => request<User>('/api/users/me/cover', { method: 'DELETE' }),

  /**
   * The home timeline. `seed` pins the server's shuffled ordering so paging
   * through one session stays consistent; omit it and the server derives a
   * per-viewer default.
   */
  feed: (scope: 'following' | 'all', page = 1, pageSize = 20, seed?: string) =>
    request<Page<Post>>(`/api/feed${query({ scope, page, page_size: pageSize, seed })}`),

  posts: (page = 1, pageSize = 20) =>
    request<Page<Post>>(`/api/posts${query({ page, page_size: pageSize })}`),

  createPost: (body: string, mediaIds: number[] = []) =>
    request<Post>('/api/posts', { method: 'POST', body: JSON.stringify({ body, media_ids: mediaIds }) }),

  getPost: (id: number) => request<Post>(`/api/posts/${id}`),

  updatePost: (id: number, body: string) =>
    request<Post>(`/api/posts/${id}`, { method: 'PATCH', body: JSON.stringify({ body }) }),

  deletePost: (id: number) => request<void>(`/api/posts/${id}`, { method: 'DELETE' }),

  likePost: (id: number) => request<Post>(`/api/posts/${id}/like`, { method: 'POST' }),

  unlikePost: (id: number) => request<Post>(`/api/posts/${id}/like`, { method: 'DELETE' }),

  // Repost endpoints return the original post, so the caller updates the post
  // it is displaying rather than the share row.
  repostPost: (id: number) => request<Post>(`/api/posts/${id}/repost`, { method: 'POST' }),

  unrepostPost: (id: number) => request<Post>(`/api/posts/${id}/repost`, { method: 'DELETE' }),

  reposts: (id: number) => request<User[]>(`/api/posts/${id}/reposts`),

  comments: (postId: number) => request<Comment[]>(`/api/posts/${postId}/comments`),

  addComment: (postId: number, body: string, parentId?: number) =>
    request<Comment>(`/api/posts/${postId}/comments`, {
      method: 'POST',
      body: JSON.stringify(parentId === undefined ? { body } : { body, parent_id: parentId }),
    }),

  deleteComment: (id: number) => request<void>(`/api/comments/${id}`, { method: 'DELETE' }),

  likeComment: (id: number) => request<Comment>(`/api/comments/${id}/like`, { method: 'POST' }),

  unlikeComment: (id: number) => request<Comment>(`/api/comments/${id}/like`, { method: 'DELETE' }),

  user: (username: string) => request<User>(`/api/users/${username}`),

  userPosts: (username: string, page = 1, pageSize = 20) =>
    request<Page<Post>>(`/api/users/${username}/posts${query({ page, page_size: pageSize })}`),

  userLikes: (username: string, page = 1, pageSize = 20) =>
    request<Page<Post>>(`/api/users/${username}/likes${query({ page, page_size: pageSize })}`),

  follow: (username: string) => request<User>(`/api/users/${username}/follow`, { method: 'POST' }),

  unfollow: (username: string) => request<User>(`/api/users/${username}/follow`, { method: 'DELETE' }),

  followers: (username: string) => request<User[]>(`/api/users/${username}/followers`),

  following: (username: string) => request<User[]>(`/api/users/${username}/following`),

  suggested: (limit = 3) => request<SuggestedUser[]>(`/api/users/suggested${query({ limit })}`),

  stats: () => request<Stats>('/api/users/stats'),

  notifications: () => request<Notification[]>('/api/notifications'),

  unreadCount: () => request<{ count: number }>('/api/notifications/unread-count'),

  markRead: (id: number) => request<Notification>(`/api/notifications/${id}/read`, { method: 'POST' }),

  markAllRead: () => request<{ updated: number }>('/api/notifications/read-all', { method: 'POST' }),

  searchPosts: (q: string, limit = 20) =>
    request<Post[]>(`/api/search/posts${query({ q, limit })}`),

  searchUsers: (q: string, limit = 20) =>
    request<User[]>(`/api/search/users${query({ q, limit })}`),

  trendingTags: (limit = 8) => request<TrendingTag[]>(`/api/trending/tags${query({ limit })}`),

  trendingPeople: (limit = 5) => request<User[]>(`/api/trending/people${query({ limit })}`),

  /** Multipart upload. Content-Type is left to the browser so the boundary is set. */
  upload: (file: File, altText = '', onProgress?: (percent: number) => void) =>
    uploadWithProgress<Media>('/api/media', file, altText, onProgress),

  conversations: () => request<Conversation[]>('/api/conversations'),

  startConversation: (username: string) =>
    request<Conversation>('/api/conversations', {
      method: 'POST',
      body: JSON.stringify({ username }),
    }),

  messages: (conversationId: number, limit = 50, beforeId?: number) =>
    request<Message[]>(
      `/api/conversations/${conversationId}/messages${query({ limit, before_id: beforeId })}`,
    ),

  sendMessage: (conversationId: number, body: string, mediaIds: number[] = []) =>
    request<Message>(`/api/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ body, media_ids: mediaIds }),
    }),

  markConversationRead: (conversationId: number) =>
    request<{ unread_count: number }>(`/api/conversations/${conversationId}/read`, {
      method: 'POST',
    }),

  messagesUnreadCount: () => request<{ count: number }>('/api/conversations/unread-count'),

  signalTyping: (conversationId: number) =>
    request<{ ok: boolean }>(`/api/conversations/${conversationId}/typing`, { method: 'POST' }),

  // ---- Admin -------------------------------------------------------------

  adminStats: () => request<AdminStats>('/api/admin/stats'),

  adminUsers: (params: { q?: string; status?: AdminUserStatus; page?: number; page_size?: number } = {}) =>
    request<Page<AdminUser>>(`/api/admin/users${query(params)}`),

  adminUser: (id: number) => request<AdminUser>(`/api/admin/users/${id}`),

  adminSetBan: (id: number, banned: boolean, reason = '') =>
    request<AdminUser>(`/api/admin/users/${id}/ban`, {
      method: 'POST',
      body: JSON.stringify({ banned, reason }),
    }),

  adminSetAdmin: (id: number, isAdmin: boolean) =>
    request<AdminUser>(`/api/admin/users/${id}/admin`, {
      method: 'POST',
      body: JSON.stringify({ is_admin: isAdmin }),
    }),

  adminSetVerified: (id: number, isVerified: boolean) =>
    request<AdminUser>(`/api/admin/users/${id}/verify`, {
      method: 'POST',
      body: JSON.stringify({ is_verified: isVerified }),
    }),

  adminDeleteUser: (id: number) => request<void>(`/api/admin/users/${id}`, { method: 'DELETE' }),

  adminPosts: (params: { q?: string; author?: string; page?: number; page_size?: number } = {}) =>
    request<Page<AdminPost>>(`/api/admin/posts${query(params)}`),

  adminDeletePost: (id: number) => request<void>(`/api/admin/posts/${id}`, { method: 'DELETE' }),

  adminComments: (params: { q?: string; author?: string; page?: number; page_size?: number } = {}) =>
    request<Page<AdminComment>>(`/api/admin/comments${query(params)}`),

  adminDeleteComment: (id: number) =>
    request<void>(`/api/admin/comments/${id}`, { method: 'DELETE' }),
}

/**
 * Upload via XMLHttpRequest rather than fetch: it is the only way to observe
 * upload progress, which matters for video files on slow connections.
 */
function uploadWithProgress<T>(
  path: string,
  file: File,
  altText: string,
  onProgress?: (percent: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('file', file)
    form.append('alt_text', altText)

    const xhr = new XMLHttpRequest()
    // Must go through apiUrl like every other request: in a split deploy the
    // API is on another origin, and a relative path would be answered by the
    // web server's SPA fallback instead (a 404, or index.html with a 200).
    xhr.open('POST', apiUrl(path))
    const token = getToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100))
      }
    }

    xhr.onload = () => {
      // Same guard as `request`: an SPA fallback answers an unknown /api path
      // with index.html, which would otherwise surface as a JSON parse error.
      const contentType = xhr.getResponseHeader('content-type') ?? ''
      if (contentType.includes('text/html')) {
        reject(new ApiError(xhr.status, htmlFallbackMessage('the upload')))
        return
      }
      const payload = xhr.responseText ? safeParse(xhr.responseText) : null
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload as T)
      } else {
        reject(new ApiError(xhr.status, readError(payload, xhr.statusText || 'Upload failed')))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, 'Network error during upload'))
    xhr.onabort = () => reject(new ApiError(0, 'Upload cancelled'))

    xhr.send(form)
  })
}
