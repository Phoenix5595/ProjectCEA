import React from 'react'
import { Flower2, FlaskConical, Settings, Sprout } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import packageJson from '../../package.json'

interface SidebarProps {
  variant: 'rail' | 'drawer'
}

interface NavItem {
  label: string
  path: string
  icon: LucideIcon
}

const navItems: NavItem[] = [
  { label: 'Laboratory', path: '/laboratory', icon: FlaskConical },
  { label: 'Vegetation', path: '/vegetation', icon: Sprout },
  { label: 'Flower', path: '/flower', icon: Flower2 },
  { label: 'Devices', path: '/devices', icon: Settings },
]

const Sidebar: React.FC<SidebarProps> = ({ variant }) => {
  const location = useLocation()

  return (
    <aside
      className={`
        fixed left-0 top-0 h-full bg-surface-secondary border-r border-border-default
        flex flex-col z-40 ${variant === 'rail' ? 'w-7.5' : 'w-52'}
      `}
    >
      <div className="flex items-center justify-center border-b border-border-default h-ribbon">
        <Link to="/">
          <img src="/logo.png" alt="CEA" className="size-7.5" />
        </Link>
      </div>

      <nav
        id="primary-navigation"
        aria-label="Primary navigation"
        className="flex-1 flex flex-col py-1 overflow-y-auto"
      >
        {navItems.map(item => {
          const isActive = location.pathname.startsWith(item.path)
          const Icon = item.icon

          return (
            <Link
              key={item.path}
              to={item.path}
              aria-label={variant === 'rail' ? item.label : undefined}
              title={item.label}
              className={`
                flex items-center gap-0.5 py-1 my-0 rounded-lg transition-colors duration-200
                ${variant === 'rail' ? 'justify-center px-0 mx-0' : 'px-1.5 mx-1'}
                ${
                  isActive
                    ? 'bg-accent-vivid text-surface-base font-medium'
                    : 'text-text-secondary hover:bg-surface-tertiary hover:text-text-default'
                }
              `}
            >
              <Icon className="size-5 shrink-0" />
              {variant === 'drawer' && (
                <span className="text-base whitespace-nowrap">{item.label}</span>
              )}
            </Link>
          )
        })}
      </nav>

      {variant === 'drawer' && (
        <div className="border-t border-border-default p-2">
          <p className="text-xs text-text-muted">v{packageJson.version}</p>
        </div>
      )}
    </aside>
  )
}

export default Sidebar
