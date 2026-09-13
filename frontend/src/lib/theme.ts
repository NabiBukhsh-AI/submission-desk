import { useEffect, useState } from 'react'

// Light, dark, or follow the system. shadcn themes on a .dark class, so the
// class is set here from the choice (or the media query) rather than the CSS
// being duplicated. The choice is a per-browser convenience, nothing more.
export type Theme = 'light' | 'dark' | 'system'

const KEY = 'sd-theme'
const media = window.matchMedia('(prefers-color-scheme: dark)')

export function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    // Storage may be unavailable; the system preference is the fallback.
  }
  return 'system'
}

export function applyTheme(theme: Theme): void {
  const dark = theme === 'dark' || (theme === 'system' && media.matches)
  document.documentElement.classList.toggle('dark', dark)
}

export function useTheme(): [Theme, (next: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(readTheme)
  useEffect(() => {
    applyTheme(theme)
    const follow = () => theme === 'system' && applyTheme(theme)
    media.addEventListener('change', follow)
    return () => media.removeEventListener('change', follow)
  }, [theme])
  const choose = (next: Theme) => {
    try {
      if (next === 'system') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, next)
    } catch {
      // Not persisted; still applied for this page.
    }
    setTheme(next)
  }
  return [theme, choose]
}
