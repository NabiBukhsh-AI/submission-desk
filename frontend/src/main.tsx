import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Dark mode follows the operating system. shadcn themes on a .dark class, so
// the class mirrors the media query rather than the CSS being duplicated.
const scheme = window.matchMedia('(prefers-color-scheme: dark)')
const apply = () => document.documentElement.classList.toggle('dark', scheme.matches)
apply()
scheme.addEventListener('change', apply)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
