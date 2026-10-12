import { useEffect, useState } from 'react'

type Theme = 'dark' | 'light'
const savedTheme = (): Theme => localStorage.getItem('oraclis-theme') as Theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(savedTheme)
  useEffect(() => { document.documentElement.dataset.theme = theme; document.documentElement.style.colorScheme = theme; localStorage.setItem('oraclis-theme', theme) }, [theme])
  return <button className="theme-toggle" type="button" aria-label={`Use ${theme === 'dark' ? 'light' : 'dark'} theme`} aria-pressed={theme === 'light'} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}><span aria-hidden="true">{theme === 'dark' ? '☀' : '☾'}</span>{theme === 'dark' ? 'Light' : 'Dark'}</button>
}
