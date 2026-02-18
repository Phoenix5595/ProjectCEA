import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { ThemeProvider, useTheme, THEME_NAMES, ThemeName } from '../contexts/ThemeContext'

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {}
  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => { store[key] = value },
    removeItem: (key: string) => { delete store[key] },
    clear: () => { store = {} },
  }
})()

Object.defineProperty(window, 'localStorage', { value: localStorageMock })

// Helper to render hook with provider
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider>{children}</ThemeProvider>
)

describe('Theme System', () => {
  beforeEach(() => {
    localStorageMock.clear()
    document.documentElement.removeAttribute('data-theme')
  })

  describe('THEME_NAMES constant', () => {
    it('contains all 6 theme names', () => {
      expect(THEME_NAMES).toHaveLength(6)
      expect(THEME_NAMES).toContain('precision-void')
      expect(THEME_NAMES).toContain('control-room')
      expect(THEME_NAMES).toContain('verdant-growth')
      expect(THEME_NAMES).toContain('spectrum')
      expect(THEME_NAMES).toContain('obsidian')
      expect(THEME_NAMES).toContain('botanical')
    })
  })

  describe('useTheme hook', () => {
    it('returns default theme as botanical', () => {
      const { result } = renderHook(() => useTheme(), { wrapper })
      expect(result.current.theme).toBe('botanical')
    })

    it('sets data-theme attribute on documentElement', () => {
      renderHook(() => useTheme(), { wrapper })
      expect(document.documentElement.getAttribute('data-theme')).toBe('botanical')
    })

    it('changes theme and updates data-theme attribute', () => {
      const { result } = renderHook(() => useTheme(), { wrapper })
      
      act(() => {
        result.current.setTheme('precision-void')
      })
      
      expect(result.current.theme).toBe('precision-void')
      expect(document.documentElement.getAttribute('data-theme')).toBe('precision-void')
    })

    it('persists theme to localStorage', () => {
      const { result } = renderHook(() => useTheme(), { wrapper })
      
      act(() => {
        result.current.setTheme('verdant-growth')
      })
      
      expect(localStorageMock.getItem('cea-theme')).toBe('verdant-growth')
    })

    it('restores theme from localStorage on mount', () => {
      localStorageMock.setItem('cea-theme', 'spectrum')
      
      const { result } = renderHook(() => useTheme(), { wrapper })
      
      expect(result.current.theme).toBe('spectrum')
    })

    it('provides themes array matching THEME_NAMES', () => {
      const { result } = renderHook(() => useTheme(), { wrapper })
      expect(result.current.themes).toEqual(THEME_NAMES)
    })
  })

  describe('ThemeName type', () => {
    it('accepts valid theme names', () => {
      const validThemes: ThemeName[] = [
        'precision-void',
        'control-room', 
        'verdant-growth',
        'spectrum',
        'obsidian',
        'botanical',
      ]
      expect(validThemes).toHaveLength(6)
    })
  })
})
