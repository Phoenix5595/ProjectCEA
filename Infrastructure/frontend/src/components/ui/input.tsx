import { forwardRef, type ComponentProps } from 'react'

import { cn } from '@/lib/utils'

export const Input = forwardRef<HTMLInputElement, ComponentProps<'input'>>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        ref={ref}
        type={type}
        data-slot="input"
        className={cn(
          'w-full rounded border border-border-default bg-surface-secondary px-2 py-1 text-text-default placeholder:text-text-faint focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-vivid',
          className,
        )}
        {...props}
      />
    )
  },
)
Input.displayName = 'Input'
