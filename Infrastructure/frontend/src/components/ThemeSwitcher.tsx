import React from 'react';
import { Palette } from 'lucide-react';
import { useTheme, ThemeName } from '../contexts/ThemeContext';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

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

  const handleChange = (value: string) => {
    setTheme(value as ThemeName);
  };

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col items-end gap-1 pointer-events-auto">
      <label htmlFor="theme-switcher" className="text-10 font-bold uppercase tracking-wider text-text-secondary opacity-70">
        <Palette className="mr-0.5 inline size-3.5" aria-hidden />
        Theme
      </label>
      <Select value={theme} onValueChange={handleChange}>
        <SelectTrigger
          id="theme-switcher"
          className="w-auto min-w-35 shadow-lg cursor-pointer"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {themes.map((t) => (
            <SelectItem key={t} value={t}>
              {themeDisplayNames[t] || t}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
};

export default ThemeSwitcher;
export { ThemeSwitcher };
