import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function Base({ size = 22, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  )
}

export const HomeIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 10.2 12 3l9 7.2" />
    <path d="M5 9.6V20a1 1 0 0 0 1 1h3.5v-5.5h5V21H18a1 1 0 0 0 1-1V9.6" />
  </Base>
)

export const SearchIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.6-3.6" />
  </Base>
)

export const BellIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M18 8.5a6 6 0 1 0-12 0c0 4.2-1.5 5.6-2 6.5h16c-.5-.9-2-2.3-2-6.5Z" />
    <path d="M10 19a2 2 0 0 0 4 0" />
  </Base>
)

export const UserIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="8.5" r="3.8" />
    <path d="M4.5 20.5c1.4-3.6 4.2-5.4 7.5-5.4s6.1 1.8 7.5 5.4" />
  </Base>
)

export const HeartIcon = ({ filled, ...p }: IconProps & { filled?: boolean }) => (
  <Base {...p} fill={filled ? 'currentColor' : 'none'}>
    <path d="M12 20s-7.5-4.6-7.5-9.6A4.4 4.4 0 0 1 12 7.3a4.4 4.4 0 0 1 7.5 3.1c0 5-7.5 9.6-7.5 9.6Z" />
  </Base>
)

export const CommentIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M20 12c0 4-3.6 7-8 7a9 9 0 0 1-2.4-.3L5 20.5l1.2-3.4A6.7 6.7 0 0 1 4 12c0-4 3.6-7 8-7s8 3 8 7Z" />
  </Base>
)

export const ShareIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 12.5 20 4l-8.5 16-.9-6.6L4 12.5Z" />
  </Base>
)

export const MoreIcon = (p: IconProps) => (
  <Base {...p} strokeWidth={2.4}>
    <circle cx="5" cy="12" r="1.1" fill="currentColor" />
    <circle cx="12" cy="12" r="1.1" fill="currentColor" />
    <circle cx="19" cy="12" r="1.1" fill="currentColor" />
  </Base>
)

export const TrashIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 7h16M9.5 7V5.4A1.4 1.4 0 0 1 10.9 4h2.2a1.4 1.4 0 0 1 1.4 1.4V7" />
    <path d="M6.5 7l.8 12.1A1.9 1.9 0 0 0 9.2 21h5.6a1.9 1.9 0 0 0 1.9-1.9L17.5 7" />
  </Base>
)

export const EditIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 20h4L20 8l-4-4L4 16v4Z" />
    <path d="m14.5 5.5 4 4" />
  </Base>
)

export const LogoutIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4" />
    <path d="M10 8 6 12l4 4M6 12h9" />
  </Base>
)

export const SparkIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.4l-1.9-5.6L4.5 11 10.1 9 12 3.5Z" />
  </Base>
)

export const LocationIcon = (p: IconProps) => (
  <Base {...p} size={p.size ?? 16}>
    <path d="M12 21s6.5-5.4 6.5-10.2A6.5 6.5 0 0 0 5.5 10.8C5.5 15.6 12 21 12 21Z" />
    <circle cx="12" cy="10.6" r="2.3" />
  </Base>
)

export const LinkIcon = (p: IconProps) => (
  <Base {...p} size={p.size ?? 16}>
    <path d="M10 14a4 4 0 0 1 0-5.6l2.4-2.4a4 4 0 1 1 5.6 5.6L16.6 13" />
    <path d="M14 10a4 4 0 0 1 0 5.6l-2.4 2.4a4 4 0 1 1-5.6-5.6L7.4 11" />
  </Base>
)

export const CalendarIcon = (p: IconProps) => (
  <Base {...p} size={p.size ?? 16}>
    <rect x="4" y="5.5" width="16" height="15" rx="2.5" />
    <path d="M4 10h16M9 3.5v4M15 3.5v4" />
  </Base>
)

export const UsersIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="9.5" cy="8.5" r="3.4" />
    <path d="M3.5 20c1.2-3.2 3.5-4.8 6-4.8s4.8 1.6 6 4.8" />
    <path d="M16.5 6.2a3 3 0 0 1 0 5.8M18 15.6c1.6.6 2.7 1.9 3.4 3.6" />
  </Base>
)

export const CloseIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="m6 6 12 12M18 6 6 18" />
  </Base>
)

export const CheckIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Base>
)

export const ArrowLeftIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M19 12H5M11 6l-6 6 6 6" />
  </Base>
)

export const ComposeIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 20h4L20 8l-4-4L4 16v4Z" />
    <path d="m14.5 5.5 4 4" />
  </Base>
)

export const TrendIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 17.5 9.5 12l3.5 3.5L20 8" />
    <path d="M15 8h5v5" />
  </Base>
)

export const ImageIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
    <circle cx="9" cy="10" r="1.6" />
    <path d="m4 17 4.5-4.5 4 4 3-3 4.5 4.5" />
  </Base>
)
