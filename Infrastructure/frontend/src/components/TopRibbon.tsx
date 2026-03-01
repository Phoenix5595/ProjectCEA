import React from 'react';
import { Link, useLocation } from 'react-router-dom';

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
  ],
  flower: [
    { id: 'overview', label: 'Overview', path: '/flower' },
    { id: 'monitoring', label: 'Monitoring', path: '/flower/monitoring' },
    { id: 'control', label: 'Control', path: '/flower/control' },
    { id: 'soil', label: 'Soil', path: '/flower/soil' },
  ],
  devices: [
    { id: 'overview', label: 'Overview', path: '/devices' },
  ],
};

const sectorEmojis: Record<Sector, string> = {
  laboratory: '🔬',
  vegetation: '🌱',
  flower: '🌻',
  devices: '⚙️',
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

  const activeTabId = activeTab || activeTabFromPath?.id || 'overview';
  const isControlPage = currentPath.includes('/control');

  return (
    <div className="w-full bg-surface-secondary border-t border-b border-border-default h-[50px] flex items-center px-4 gap-4 sticky top-0 z-10">
      {roomName && (
        <h1 className="text-base font-bold text-default flex items-center gap-2 whitespace-nowrap">
          <span>{sectorEmojis[sector]}</span>
          {roomName}
        </h1>
      )}

      <nav className="flex overflow-x-auto scrollbar-hide">
        <div className="flex min-w-max">
          {tabs.map((tab) => {
            const isActive = activeTabId === tab.id;
            return (
              <Link
                key={tab.id}
                to={tab.path}
                onClick={() => onTabChange(tab.id)}
                className={`
                  relative px-3 py-1.5 text-sm font-medium whitespace-nowrap
                  transition-all duration-200 rounded
                  ${
                    isActive
                      ? 'bg-accent text-surface-base'
                      : 'text-text-secondary hover:text-default hover:bg-surface-tertiary'
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
        <div className="flex items-center gap-3 ml-auto">
          {(saveError || saveSuccess) && (
            <div className={`text-xs px-2 py-0.5 rounded ${saveError ? 'bg-status-danger-bg text-status-danger-text' : 'bg-status-success-bg text-status-success-text'}`}>
              {saveError || saveSuccess}
            </div>
          )}
          <button
            onClick={onSave}
            disabled={saving}
            className="px-3 py-1 bg-accent-vivid hover:bg-accent-hover text-text-default text-xs font-bold rounded transition-colors"
          >
            {saving ? '...' : 'SAVE'}
          </button>
          {currentMode && onModeChange && (
              <select
                value={currentMode.submode_name ? `${currentMode.mode_name}:${currentMode.submode_name}` : currentMode.mode_name || ''}
                onChange={(e) => {
                  const [mode, submode] = e.target.value.split(':');
                  onModeChange(mode, submode);
                }}
                className="bg-surface-secondary text-text-secondary text-xs px-2 py-1 rounded border border-border-default"
              >
                <option value="veg">Veg</option>
                <option value="flower:stretch">Flower - Stretch</option>
                <option value="flower:bulk">Flower - Bulk</option>
                <option value="flower:ripen">Flower - Ripen</option>
                <option value="drying">Drying</option>
                <option value="sleep">Sleep</option>
              </select>
          )}
        </div>
      )}
    </div>
  );
};

export default TopRibbon;
