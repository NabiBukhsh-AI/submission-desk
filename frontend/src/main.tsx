import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { applyTheme, readTheme } from './lib/theme'

// Applied before the first paint so the page never flashes the wrong theme.
applyTheme(readTheme())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
