/**
 * The Pulse mark, in the two forms the app needs.
 *
 * The geometry is identical to the favicon and the app icon, so the thing a
 * visitor sees while the bundle boots is the same mark they just clicked. Both
 * forms share one path and one gradient definition, so they cannot drift.
 */

import { useId } from 'react'

type PulseLogoProps = {
  size?: number
  animated?: boolean
  className?: string
}

/** Path length is ~31.4 units, rounded up so the trace starts fully hidden. */
const TRACE_LENGTH = 32

const WAVEFORM = 'M3.6 12h4.4l1.6-4.8 2.2 9.6 1.6-4.8h7'

/**
 * The gradient is lime to sky, the two accents the app already uses for the
 * brand. The id comes from `useId` because several marks render at once and a
 * repeated id would make every one of them resolve to the first gradient in
 * the document.
 */
function useFill() {
  return `pulseFill${useId().replace(/:/g, '')}`
}

function Waveform() {
  return (
    <path
      d={WAVEFORM}
      fill="none"
      stroke="#10160a"
      strokeWidth="2.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  )
}

/**
 * The mark, optionally animated for the first-load screen.
 *
 * The animation is the trace drawing itself across the tile while a halo
 * breathes behind it, which is the reading of the word "pulse" the logo is
 * built on. It says what is loading, unlike an indeterminate spinner.
 */
export function PulseLogo({ size = 96, animated = false, className }: PulseLogoProps) {
  const fill = useFill()
  const classes = ['pulse-logo', animated && 'pulse-logo-animated', className]
    .filter(Boolean)
    .join(' ')

  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className={classes}
      role="img"
      aria-label="Pulse"
    >
      <defs>
        <linearGradient id={fill} x1="0" y1="0" x2="0.86" y2="1">
          <stop offset="0" stopColor="#b8f24a" />
          <stop offset="0.55" stopColor="#8ce08f" />
          <stop offset="1" stopColor="#60a5fa" />
        </linearGradient>
      </defs>
      {animated && <rect className="pulse-logo-halo" width="24" height="24" rx="7" />}
      <rect className="pulse-logo-tile" width="24" height="24" rx="7" fill={`url(#${fill})`} />
      <g className="pulse-logo-trace" strokeDasharray={animated ? TRACE_LENGTH : undefined}>
        <Waveform />
      </g>
    </svg>
  )
}

/** The static mark, for the brand chrome: the sidebar lockup and auth screens. */
export function PulseMark({ size = 34, className }: { size?: number; className?: string }) {
  const fill = useFill()
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className={['pulse-mark', className].filter(Boolean).join(' ')}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={fill} x1="0" y1="0" x2="0.86" y2="1">
          <stop offset="0" stopColor="#b8f24a" />
          <stop offset="0.55" stopColor="#8ce08f" />
          <stop offset="1" stopColor="#60a5fa" />
        </linearGradient>
      </defs>
      <rect width="24" height="24" rx="7" fill={`url(#${fill})`} />
      <Waveform />
    </svg>
  )
}
