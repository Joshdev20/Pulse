export interface User {
  id: number
  username: string
  display_name: string
  bio: string
  location: string
  website: string
  avatar_color: string
  created_at: string
  follower_count: number
  following_count: number
  post_count: number
  is_following: boolean
  is_admin: boolean
}

export interface Tag {
  id: number
  name: string
}

export interface Media {
  id: number
  kind: 'image' | 'video' | 'application'
  content_type: string
  size_bytes: number
  width: number | null
  height: number | null
  alt_text: string
  url: string
}

export interface Post {
  id: number
  body: string
  created_at: string
  edited_at: string | null
  author: User
  tags: Tag[]
  media: Media[]
  like_count: number
  comment_count: number
  liked_by_me: boolean
}

export interface Comment {
  id: number
  body: string
  created_at: string
  author: User
}

export interface Message {
  id: number
  conversation_id: number
  body: string
  created_at: string
  sender: User
  media: Media[]
  mine: boolean
}

export interface Conversation {
  id: number
  created_at: string
  last_message_at: string
  partner: User
  last_message: Message | null
  unread_count: number
}

export interface RealtimeNotification {
  id: number
  kind: 'like' | 'comment' | 'follow'
  read: boolean
  created_at: string
  post_id: number | null
  actor_id: number | null
}

export interface Notification {
  id: number
  kind: 'like' | 'comment' | 'follow'
  read: boolean
  created_at: string
  post_id: number | null
  actor: User | null
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  has_more: boolean
}

export interface SuggestedUser {
  user: User
  reason: string
}

export interface Stats {
  users: number
  posts: number
  likes: number
  comments: number
}

export interface TrendingTag {
  name: string
  uses: number
}

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

export interface AdminUser {
  id: number
  username: string
  email: string
  display_name: string
  bio: string
  location: string
  website: string
  avatar_color: string
  created_at: string
  is_active: boolean
  is_admin: boolean
  is_banned: boolean
  banned_reason: string
  follower_count: number
  following_count: number
  post_count: number
  comment_count: number
}

export interface AdminPost {
  id: number
  body: string
  created_at: string
  author_id: number
  author_username: string
  author_display_name: string
  like_count: number
  comment_count: number
  media_count: number
}

export interface AdminComment {
  id: number
  body: string
  created_at: string
  post_id: number
  author_id: number
  author_username: string
  author_display_name: string
}

export interface AdminCount {
  label: string
  value: number
}

export interface AdminStats {
  total_users: number
  active_users: number
  banned_users: number
  admin_users: number
  new_users_7d: number
  new_users_30d: number
  total_posts: number
  posts_7d: number
  posts_30d: number
  total_comments: number
  total_likes: number
  total_messages: number
  total_conversations: number
  total_media: number
  engagement_rate: number
  avg_posts_per_user: number
  top_posters: AdminCount[]
  top_tags: AdminCount[]
}

export type AdminUserStatus = 'all' | 'active' | 'banned' | 'admin'
