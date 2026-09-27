import type { ReactNode } from 'react'

/** Matches sidebar logo band height (`Sidebar` header/footer). */
export const APP_RIBBON_HEIGHT_PX = 30

const RIBBON_BASE =
  'min-w-0 shrink-0 w-full flex items-center gap-1 px-2 bg-surface-secondary border-border-default'

export interface AppRibbonProps {
  position: 'top' | 'bottom'
  children: ReactNode
  className?: string
  sticky?: boolean
  wrap?: boolean
}

export function AppRibbon({
  position,
  children,
  className = '',
  sticky = false,
  wrap = false,
}: AppRibbonProps) {
  const border = position === 'top' ? 'border-b' : 'border-t'
  const stickyClass = sticky && position === 'top' ? 'sticky top-0 z-10' : ''
  const layoutClass = wrap ? 'flex-wrap min-h-ribbon' : 'h-ribbon overflow-x-auto'

  return (
    <div className={`${RIBBON_BASE} ${border} ${layoutClass} ${stickyClass} ${className}`.trim()}>
      {children}
    </div>
  )
}
