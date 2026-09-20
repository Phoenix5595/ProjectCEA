import React, { useEffect, useState } from 'react';
import { ChevronsLeft, Flower2, FlaskConical, Settings, Sprout } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import packageJson from '../../package.json';

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

interface NavItem {
  label: string;
  path: string;
  icon: LucideIcon;
}

const navItems: NavItem[] = [
  { label: 'Laboratory', path: '/laboratory', icon: FlaskConical },
  { label: 'Vegetation', path: '/vegetation', icon: Sprout },
  { label: 'Flower', path: '/flower', icon: Flower2 },
  { label: 'Devices', path: '/devices', icon: Settings },
];

const Sidebar: React.FC<SidebarProps> = ({ collapsed, onToggle }) => {
  const location = useLocation();

  const collapsibleItems = navItems;

  return (
    <aside
      className={`
        fixed left-0 top-0 h-full
        bg-surface-secondary border-r border-border-default
        flex flex-col
        transition-[width] duration-300 ease-in-out
        z-40
        ${collapsed ? 'w-7.5' : 'w-52'}
      `}
    >
      {/* Logo / Header */}
      <div className="flex items-center justify-center border-b border-border-default h-ribbon">
        <Link to="/">
          <img src="/logo.png" alt="CEA" className="size-7.5" />
        </Link>
      </div>

      {/* Navigation */}
      <nav className="flex-1 flex flex-col py-1 overflow-y-auto">

        {/* Divider */}
        {!collapsed && (
          <div className="mx-3 my-2 border-t border-border-default" />
        )}

        {/* Collapsible Items */}
        {collapsibleItems.map((item) => {
          const isActive = location.pathname.startsWith(item.path);
          const Icon = item.icon;
          
          return (
            <Link
              key={item.path}
              to={item.path}
              className={`
                flex items-center gap-0.5 py-1 my-0 rounded-lg
                transition-colors duration-200
                ${collapsed ? 'justify-center px-0 mx-0' : 'px-1.5 mx-1'}
                ${
                  isActive
                    ? 'bg-accent-vivid text-surface-base font-medium'
                    : 'text-text-secondary hover:bg-surface-tertiary hover:text-text-default'
                }
              `}
              title={collapsed ? item.label : undefined}
            >
              <Icon className="size-5 shrink-0" />
              {!collapsed && (
                <span className="text-base whitespace-nowrap">{item.label}</span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className={`
        border-t border-border-default flex flex-col gap-2
        ${collapsed ? 'items-center p-0.5' : 'p-2'}
      `}>
        {!collapsed && (
          <p className="text-xs text-text-muted">
            v{packageJson.version}
          </p>
        )}
        <button
          type="button"
          onClick={onToggle}
          className={`
            ${collapsed ? 'p-1' : 'p-1.5'} rounded-md
            hover:bg-surface-tertiary
            text-text-secondary hover:text-text-default
            transition-colors duration-200
            ${collapsed ? '' : 'self-start'}
          `}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <ChevronsLeft
            className={`size-5 transition-transform duration-300 ${collapsed ? 'rotate-180' : ''}`}
          />
        </button>
      </div>
    </aside>
  );
};

// Hook to manage sidebar collapsed state with localStorage persistence
export const useSidebarState = () => {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    const stored = localStorage.getItem('cea-sidebar-collapsed');
    return stored === 'true';
  });

  useEffect(() => {
    localStorage.setItem('cea-sidebar-collapsed', String(collapsed));
  }, [collapsed]);

  const toggle = () => setCollapsed((prev) => !prev);

  return { collapsed, toggle };
};

export default Sidebar;
