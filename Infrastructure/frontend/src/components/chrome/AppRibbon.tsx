import type { ReactNode } from 'react';

/** Matches sidebar logo band height (`Sidebar` header/footer). */
export const APP_RIBBON_HEIGHT_PX = 50;

const RIBBON_BASE =
  'shrink-0 w-full flex items-center gap-2 px-2 bg-surface-secondary border-border-default overflow-hidden';

export interface AppRibbonProps {
  position: 'top' | 'bottom';
  children: ReactNode;
  className?: string;
  sticky?: boolean;
}

export function AppRibbon({ position, children, className = '', sticky = false }: AppRibbonProps) {
  const border = position === 'top' ? 'border-b' : 'border-t';
  const stickyClass = sticky && position === 'top' ? 'sticky top-0 z-10' : '';

  return (
    <div
      className={`${RIBBON_BASE} ${border} h-[50px] ${stickyClass} ${className}`.trim()}
      style={{ minHeight: `${APP_RIBBON_HEIGHT_PX}px`, maxHeight: `${APP_RIBBON_HEIGHT_PX}px` }}
    >
      {children}
    </div>
  );
}
