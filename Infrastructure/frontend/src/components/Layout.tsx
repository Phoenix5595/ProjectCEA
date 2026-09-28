import { useState, type FC } from 'react'
import { Outlet, useLocation } from 'react-router-dom'

import { useControlActions } from '../contexts/ControlActionsContext'

import Sidebar from './Sidebar'
import TopRibbon, { Sector } from './TopRibbon'

// Map pathname to sector
const getSectorFromPath = (pathname: string): Sector | null => {
  if (pathname.startsWith('/laboratory')) return 'laboratory'
  if (pathname.startsWith('/vegetation')) return 'vegetation'
  if (pathname.startsWith('/flower')) return 'flower'
  if (pathname.startsWith('/devices')) return 'devices'
  return null
}

const Layout: FC = () => {
  const location = useLocation()
  const { actions } = useControlActions()

  const [activeTab, setActiveTab] = useState<string>('overview')

  // Determine sector from current path
  const sector = getSectorFromPath(location.pathname)
  const showTopRibbon = sector !== null

  return (
    <div className="min-h-screen bg-surface-base">
      <Sidebar />
      {/* Main Content */}
      <div className="transition-[margin] duration-300 ease-in-out ml-7.5">
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
        <main className="p-0">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export default Layout
