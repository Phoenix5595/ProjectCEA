import type { ButtonHTMLAttributes, ReactNode } from 'react';

/** Icon/action control sized like the sidebar collapse button. */
export function RibbonMenuButton({
  children,
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode }) {
  return (
    <button
      type="button"
      className={`shrink-0 p-1.5 rounded-md hover:bg-surface-tertiary text-text-secondary hover:text-text-default transition-colors ${className}`.trim()}
      {...props}
    >
      {children}
    </button>
  );
}
