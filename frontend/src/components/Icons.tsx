// The few icons the battle report shell draws itself: one stroke weight, inherits
// the text colour, sized in em so it tracks the surrounding type.
interface IconProps {
  className?: string
}

const base = {
  width: '1em', height: '1em', viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.75, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  'aria-hidden': true, focusable: false,
}

/** Disclosure caret: points right when closed, down when open. */
export function Caret({ open, className }: IconProps & { open: boolean }) {
  return (
    <svg {...base} className={className} style={{ transform: open ? 'rotate(90deg)' : undefined }}>
      <path d="M6 3.5 10.5 8 6 12.5" />
    </svg>
  )
}

/** Sort direction: an arrow pointing down (descending) or up (ascending). */
export function SortArrow({ desc, className }: IconProps & { desc: boolean }) {
  return (
    <svg {...base} className={className} style={{ transform: desc ? undefined : 'rotate(180deg)' }}>
      <path d="M8 3v10M4 9l4 4 4-4" />
    </svg>
  )
}

export function PencilIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M11 2.5 13.5 5 5.5 13H3v-2.5z" />
    </svg>
  )
}

export function ArrowLeftIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M13 8H3M7 4 3 8l4 4" />
    </svg>
  )
}

export function ExternalIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M9 3h4v4M13 3 7.5 8.5M11 9.5V13H3V5h3.5" />
    </svg>
  )
}
