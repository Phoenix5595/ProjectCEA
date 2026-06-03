import React from 'react';
import { Link, useLocation } from 'react-router-dom';

import { AppRibbon } from './chrome/AppRibbon';

export type Sector = 'laboratory' | 'vegetation' | 'flower' | 'devices';

interface TopRibbonProps {
  sector: Sector;
  activeTab: string;
  onTabChange: (tab: string) => void;
  roomName?: string;
  showActions?: boolean;
  onSave?: () => void;
  saving?: boolean;
  saveSuccess?: string | null;
  saveError?: string | null;
  currentMode?: { mode_name?: string; submode_name?: string | null } | null;
  onModeChange?: (mode: string, submode?: string) => void;
}

interface Tab {
  id: string;
  label: string;
  path: string;
}

const sectorTabs: Record<Sector, Tab[]> = {
  laboratory: [
    { id: 'overview', label: 'Overview', path: '/laboratory' },
    { id: 'climate', label: 'Climate', path: '/laboratory/climate' },
    { id: 'water', label: 'Water', path: '/laboratory/water' },
    { id: 'infrastructure', label: 'Infrastructure', path: '/laboratory/infrastructure' },
  ],
  vegetation: [
    { id: 'overview', label: 'Overview', path: '/vegetation' },
    { id: 'monitoring', label: 'Monitoring', path: '/vegetation/monitoring' },
    { id: 'control', label: 'Control', path: '/vegetation/control' },
    { id: 'automation', label: 'Automation', path: '/vegetation/automation' },
  ],
  flower: [
    { id: 'overview', label: 'Overview', path: '/flower' },
    { id: 'monitoring', label: 'Monitoring', path: '/flower/monitoring' },
    { id: 'control', label: 'Control', path: '/flower/control' },
    { id: 'automation', label: 'Automation', path: '/flower/automation' },
    { id: 'soil', label: 'Soil', path: '/flower/soil' },
  ],
  devices: [{ id: 'overview', label: 'Overview', path: '/devices' }],
};

const sectorEmojis: Record<Sector, string> = {
  laboratory: '🔬',
  vegetation: '🌱',
  flower: '🌻',
  devices: '⚙️',
};

const sectorDefaultNames: Record<Sector, string> = {
  laboratory: 'Laboratory',
  vegetation: 'Vegetation Room',
  flower: 'Flower Room',
  devices: 'Device Configuration',
};

const TopRibbon: React.FC<TopRibbonProps> = ({
  sector,
  activeTab,
  onTabChange,
  roomName,
  showActions = false,
  onSave,
  saving = false,
  saveSuccess,
  saveError,
  currentMode,
  onModeChange,
}) => {
  const location = useLocation();
  const tabs = sectorTabs[sector];

  const currentPath = location.pathname;
  const activeTabFromPath = tabs.find((tab) => {
    if (tab.id === 'overview') {
      return currentPath === tab.path;
    }
    return currentPath.startsWith(tab.path);
  });

  const activeTabId = activeTabFromPath?.id || activeTab || 'overview';
  const isControlPage = currentPath.includes('/control');

  const displayRoomName = roomName || sectorDefaultNames[sector];

  return (
    <AppRibbon position="top" sticky>
      <h1 className="text-base font-bold text-text-default flex items-center gap-1 whitespace-nowrap shrink-0">
        <span className="text-xl leading-none">{sectorEmojis[sector]}</span>
        {displayRoomName}
      </h1>

      <nav className="flex overflow-x-auto scrollbar-hide min-w-0 flex-1">
        <div className="flex min-w-max gap-0.5">
          {tabs.map((tab) => {
            const isActive = activeTabId === tab.id;
            return (
              <Link
                key={tab.id}
                to={tab.path}
                onClick={() => onTabChange(tab.id)}
                className={`
                  px-1.5 py-1 text-sm font-medium whitespace-nowrap
                  transition-all duration-200 rounded-lg
                  ${
                    isActive
                      ? 'bg-accent text-surface-base'
                      : 'text-text-secondary hover:text-text-default hover:bg-surface-tertiary'
                  }
                `}
              >
                {tab.label}
              </Link>
            );
          })}
        </div>
      </nav>

      {showActions && isControlPage && (
        <div className="flex items-center gap-1 ml-auto shrink-0">
          {currentMode && onModeChange && (
            <>
              <div className="flex gap-1">
                {['veg', 'flower', 'drying', 'sleep'].map((mode) => (
                  <button
                    key={mode}
                    onClick={() => onModeChange(mode)}
                    className={`px-2 py-0.5 text-sm font-bold rounded border transition-all ${
                      currentMode.mode_name === mode
                        ? 'bg-accent text-white border-accent'
                        : 'bg-transparent text-text-default border-border-default hover:bg-surface-tertiary hover:border-border-emphasis'
                    }`}
                  >
                    {mode.charAt(0).toUpperCase() + mode.slice(1)}
                  </button>
                ))}
              </div>
              {currentMode.mode_name === 'flower' && (
                <div className="flex gap-1 ml-2">
                  {['stretch', 'bulk', 'ripen'].map((sub) => (
                    <button
                      key={sub}
                      onClick={() => onModeChange('flower', sub)}
                      className={`px-2 py-0.5 text-sm font-bold rounded border transition-all ${
                        currentMode.submode_name === sub
                          ? 'bg-accent-vivid text-white border-accent-vivid'
                          : 'bg-transparent text-text-default border-border-default hover:bg-surface-tertiary hover:border-border-emphasis'
                      }`}
                    >
                      {sub.charAt(0).toUpperCase() + sub.slice(1)}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
          {(saveError || saveSuccess) && (
            <div
              className={`text-xs px-2 py-0.5 rounded ${
                saveError
                  ? 'bg-status-danger-bg text-status-danger-text'
                  : 'bg-status-success-bg text-status-success-text'
              }`}
            >
              {saveError || saveSuccess}
            </div>
          )}
          <button
            onClick={onSave}
            disabled={saving}
            className="px-2 py-0.5 bg-accent-vivid hover:bg-accent-hover text-text-default text-xs font-bold rounded transition-colors"
          >
            {saving ? '...' : 'SAVE'}
          </button>
        </div>
      )}
    </AppRibbon>
  );
};

export default TopRibbon;
