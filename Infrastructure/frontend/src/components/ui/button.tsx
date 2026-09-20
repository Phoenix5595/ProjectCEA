import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { forwardRef, type ComponentProps } from 'react'

import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex select-none items-center justify-center gap-1 rounded-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-vivid/50 disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary: 'bg-btn-primary text-white hover:bg-btn-primary-hover',
        accent: 'bg-accent-vivid text-surface-base hover:bg-accent-hover',
        'accent-strong':
          'bg-accent-active text-text-default hover:bg-accent-hover disabled:bg-surface-secondary disabled:text-text-faint disabled:opacity-100',
        outline:
          'border border-border-default bg-transparent text-text-default hover:bg-surface-tertiary hover:border-border-emphasis',
        ghost: 'text-text-secondary hover:bg-surface-tertiary hover:text-text-default',
        destructive: 'bg-status-danger-vivid text-white hover:bg-status-danger-vivid/90',
      },
      size: {
        default: 'px-3 py-1.5 text-sm',
        sm: 'px-2 py-1 text-xs',
        icon: 'p-1.5',
      },
    },
    defaultVariants: {
      variant: 'ghost',
      size: 'default',
    },
  },
)

export interface ButtonProps extends ComponentProps<'button'>, VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        ref={ref}
        data-slot="button"
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    )
  },
)
Button.displayName = 'Button'

export { buttonVariants }
