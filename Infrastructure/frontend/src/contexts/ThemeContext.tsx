import { createContext, useContext, useEffect, useState, ReactNode } from 'react'

export const THEME_NAMES = [
  'precision-void',
  'control-room',
  'verdant-growth',
  'spectrum',
  'obsidian',
  'botanical'
] as const

export type ThemeName = (typeof THEME_NAMES)[number]

interface ThemeContextType {
  theme: ThemeName
  setTheme: (theme: ThemeName) => void
  themes: typeof THEME_NAMES
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<ThemeName>(() => {
    const stored = localStorage.getItem('cea-theme') as ThemeName | null
    if (stored && THEME_NAMES.includes(stored)) return stored
    return 'botanical'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('cea-theme', theme)
  }, [theme])

  return (
    <ThemeContext.Provider value={{ theme, setTheme, themes: THEME_NAMES }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider')
  }
  return context
}

