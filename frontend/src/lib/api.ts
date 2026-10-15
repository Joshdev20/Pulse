import type {
  Comment,
  Notification,
  Page,
  Post,
  Stats,
  SuggestedUser,
  TrendingTag,
  User,
} from './types'

const TOKEN_KEY = 'pulse.token'

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

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(path, { ...options, headers })

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

  updateProfile: (username: string, data: Partial<Pick<User, 'display_name' | 'bio' | 'location' | 'website' | 'avatar_color'>>) =>
    request<User>(`/api/users/${username}`, { method: 'PATCH', body: JSON.stringify(data) }),

  feed: (scope: 'following' | 'all', page = 1, pageSize = 20) =>
    request<Page<Post>>(`/api/feed${query({ scope, page, page_size: pageSize })}`),

  posts: (page = 1, pageSize = 20) =>
    request<Page<Post>>(`/api/posts${query({ page, page_size: pageSize })}`),

  createPost: (body: string) =>
    request<Post>('/api/posts', { method: 'POST', body: JSON.stringify({ body }) }),

  getPost: (id: number) => request<Post>(`/api/posts/${id}`),

  updatePost: (id: number, body: string) =>
    request<Post>(`/api/posts/${id}`, { method: 'PATCH', body: JSON.stringify({ body }) }),

  deletePost: (id: number) => request<void>(`/api/posts/${id}`, { method: 'DELETE' }),

  likePost: (id: number) => request<Post>(`/api/posts/${id}/like`, { method: 'POST' }),

  unlikePost: (id: number) => request<Post>(`/api/posts/${id}/like`, { method: 'DELETE' }),

  comments: (postId: number) => request<Comment[]>(`/api/posts/${postId}/comments`),

  addComment: (postId: number, body: string) =>
    request<Comment>(`/api/posts/${postId}/comments`, { method: 'POST', body: JSON.stringify({ body }) }),

  deleteComment: (id: number) => request<void>(`/api/comments/${id}`, { method: 'DELETE' }),

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
}
