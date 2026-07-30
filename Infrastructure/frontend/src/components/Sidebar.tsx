import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

interface NavItem {
  label: string;
  path: string;
  icon: string;
}

const navItems: NavItem[] = [
  { label: 'Laboratory', path: '/laboratory', icon: '🔬' },
  { label: 'Vegetation', path: '/vegetation', icon: '🌱' },
  { label: 'Flower', path: '/flower', icon: '🌻' },
  { label: 'Devices', path: '/devices', icon: '⚙️' },
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
        transition-all duration-300 ease-in-out
        z-40
        ${collapsed ? 'w-12' : 'w-52'}
      `}
      style={{
        width: collapsed ? '3rem' : '13rem',
      }}
    >
      {/* Logo / Header */}
      <div className="flex items-center justify-center px-1 py-1 border-b border-border-default h-[50px]">
        <Link to="/">
          <img src="/logo.png" alt="CEA" className="w-6 h-6" />
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
          
          return (
            <Link
              key={item.path}
              to={item.path}
              className={`
                flex items-center gap-0.5 py-1 my-0 rounded-lg
                transition-all duration-200
                ${collapsed ? 'justify-center px-0 mx-0' : 'px-1.5 mx-1'}
                ${
                  isActive
                    ? 'bg-accent text-surface-base font-medium'
                    : 'text-text-secondary hover:bg-surface-tertiary hover:text-default'
                }
              `}
              title={collapsed ? item.label : undefined}
            >
              <span className="text-xl flex-shrink-0">{item.icon}</span>
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
        ${collapsed ? 'items-center p-1.5' : 'p-2'}
      `}>
        {!collapsed && (
          <p className="text-xs text-text-muted">
            v1.0.0
          </p>
        )}
        <button
          onClick={onToggle}
          className={`
            p-1.5 rounded-md
            hover:bg-surface-tertiary
            text-text-secondary hover:text-default
            transition-colors duration-200
            ${collapsed ? '' : 'self-start'}
          `}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <svg
            className={`w-5 h-5 transition-transform duration-300 ${collapsed ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M11 19l-7-7 7-7m8 14l-7-7 7-7"
            />
          </svg>
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
