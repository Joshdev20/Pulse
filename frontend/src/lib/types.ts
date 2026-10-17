export interface User {
  id: number
  username: string
  display_name: string
  bio: string
  location: string
  website: string
  avatar_color: string
  avatar_url: string | null
  /** Profile header image; null renders the plain gradient banner. */
  cover_url: string | null
  created_at: string
  follower_count: number
  following_count: number
  post_count: number
  is_following: boolean
  is_admin: boolean
  is_verified: boolean
  /**
   * Presence, present only on a conversation partner payload. The API omits
   * both fields elsewhere (search, profiles, followers) so a user cannot be
   * tracked as online outside a thread they share.
   */
  last_seen_at?: string | null
  is_online?: boolean
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
  repost_count: number
  reposted_by_me: boolean
  /**
   * Set when this row is a repost: `author` and `created_at` describe who
   * shared it, while this holds the post that was shared. Null for an
   * original, and always exactly one level deep.
   */
  repost_of: Post | null
}

export interface Comment {
  id: number
  body: string
  created_at: string
  author: User
  parent_id: number | null
  like_count: number
  liked_by_me: boolean
  replies: Comment[]
}

export interface Message {
  id: number
  conversation_id: number
  body: string
  created_at: string
  sender: User
  media: Media[]
  mine: boolean
  /** Set when this message was a swipe-to-reply to another one. */
  reply_to: MessageQuote | null
  like_count: number
  liked_by_me: boolean
}

/** The one-line quote shown above a reply. */
export interface MessageQuote {
  id: number
  body: string
  sender_username: string
  sender_display_name: string
  has_media: boolean
}

export interface Conversation {
  id: number
  created_at: string
  last_message_at: string
  partner: User
  last_message: Message | null
  unread_count: number
}

/** A notification the API can produce. `repost` is someone sharing your post. */
export type NotificationKind = 'like' | 'comment' | 'follow' | 'repost'

export interface RealtimeNotification {
  id: number
  kind: NotificationKind
  read: boolean
  created_at: string
  post_id: number | null
  actor_id: number | null
}

export interface Notification {
  id: number
  kind: NotificationKind
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
  avatar_url: string | null
  cover_url: string | null
  created_at: string
  is_active: boolean
  is_admin: boolean
  is_banned: boolean
  banned_reason: string
  is_verified: boolean
  verified_at: string | null
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
