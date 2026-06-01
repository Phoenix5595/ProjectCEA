import React, { useState, useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar, { useSidebarState } from './Sidebar';
import TopRibbon, { Sector } from './TopRibbon';
import { useControlActions } from '../contexts/ControlActionsContext';

const MOBILE_BREAKPOINT = 768;

// Map pathname to sector
const getSectorFromPath = (pathname: string): Sector | null => {
  if (pathname.startsWith('/laboratory')) return 'laboratory';
  if (pathname.startsWith('/vegetation')) return 'vegetation';
  if (pathname.startsWith('/flower')) return 'flower';
  if (pathname.startsWith('/devices')) return 'devices';
  return null;
};

const Layout: React.FC = () => {
  const location = useLocation();
  const { collapsed, toggle: toggleSidebar } = useSidebarState();
  const { actions } = useControlActions();
  
  const [isMobile, setIsMobile] = useState<boolean>(false);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState<boolean>(false);
  const [activeTab, setActiveTab] = useState<string>('overview');

  // Check for mobile viewport
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    };
    
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Determine sector from current path
  const sector = getSectorFromPath(location.pathname);
  const showTopRibbon = sector !== null;
  const isDashboard = location.pathname === '/';

  // Close mobile drawer on route change
  useEffect(() => {
    setMobileDrawerOpen(false);
  }, [location.pathname]);

  // Handle hamburger menu toggle
  const handleHamburgerClick = () => {
    setMobileDrawerOpen((prev) => !prev);
  };

  // Handle sidebar toggle (works for both mobile and desktop)
  const handleSidebarToggle = () => {
    if (isMobile) {
      setMobileDrawerOpen((prev) => !prev);
    } else {
      toggleSidebar();
    }
  };

  return (
    <div className="min-h-screen bg-surface-base">
      {/* Mobile Hamburger Button */}
      {isMobile && (
        <button
          onClick={handleHamburgerClick}
          className="fixed top-3 left-3 z-50 p-2 rounded-md bg-surface-secondary border border-border-default hover:bg-surface-tertiary"
          aria-label={mobileDrawerOpen ? 'Close menu' : 'Open menu'}
        >
          <svg
            className="w-6 h-6 text-default"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            {mobileDrawerOpen ? (
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            ) : (
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 6h16M4 12h16M4 18h16"
              />
            )}
          </svg>
        </button>
      )}

      {/* Mobile Overlay */}
      {isMobile && mobileDrawerOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-30"
          onClick={() => setMobileDrawerOpen(false)}
        />
      )}

      {/* Sidebar */}
      {(!isMobile || mobileDrawerOpen) && (
        <div
          className={`
            ${isMobile ? 'fixed left-0 top-0 h-full z-40' : ''}
          `}
        >
          <Sidebar
            collapsed={isMobile ? false : collapsed}
            onToggle={handleSidebarToggle}
          />
        </div>
      )}

      {/* Main Content */}
      <div
        className={`
          transition-all duration-300 ease-in-out
          ${isMobile ? 'ml-0' : collapsed ? 'ml-12' : 'ml-52'}
        `}
      >
        {/* TopRibbon - only shown on sector pages */}
        {showTopRibbon && sector && (
          <TopRibbon
            sector={sector}
            activeTab={activeTab}
            onTabChange={setActiveTab}
            roomName={actions.roomName}
            showActions={actions.showActions}
            onSave={actions.onSave}
            saving={actions.saving}
            saveSuccess={actions.saveSuccess}
            saveError={actions.saveError}
            currentMode={actions.currentMode}
            onModeChange={actions.onModeChange}
          />
        )}

        {/* Page Content — dashboard is full-bleed so top/bottom ribbons align with sidebar chrome */}
        <main className={isDashboard ? 'p-0' : 'p-4'}>
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default Layout;
