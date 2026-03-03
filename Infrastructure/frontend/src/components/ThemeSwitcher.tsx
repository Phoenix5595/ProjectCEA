import React from 'react';
import { useTheme, ThemeName } from '../contexts/ThemeContext';

const ThemeSwitcher: React.FC = () => {
  if (!import.meta.env.DEV) {
    return null;
  }

  const { theme, setTheme, themes } = useTheme();

  const themeDisplayNames: Record<ThemeName, string> = {
    'precision-void': 'Precision Void',
    'control-room': 'Control Room',
    'verdant-growth': 'Verdant Growth',
    'spectrum': 'Spectrum Analytics',
    'obsidian': 'Obsidian Glass',
    'botanical': 'Botanical'
  };

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setTheme(e.target.value as ThemeName);
  };

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col items-end gap-1 pointer-events-auto">
      <label htmlFor="theme-switcher" className="text-[10px] font-bold uppercase tracking-wider text-secondary opacity-70">
        🎨 Theme
      </label>
      <select
        id="theme-switcher"
        value={theme}
        onChange={handleChange}
        className="bg-surface-secondary text-default border border-default rounded px-2 py-1 text-sm shadow-lg focus:outline-none focus:ring-1 focus:ring-accent appearance-none cursor-pointer"
        style={{ minWidth: '140px' }}
      >
        {themes.map((t) => (
          <option key={t} value={t}>
            {themeDisplayNames[t] || t}
          </option>
        ))}
      </select>
    </div>
  );
};

export default ThemeSwitcher;
export { ThemeSwitcher };
