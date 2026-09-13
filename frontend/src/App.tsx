import { useEffect, useState } from 'react'
import {
  BookOpenCheck,
  FlaskConical,
  Home as HomeIcon,
  Inbox,
  LogIn,
  LogOut,
  Monitor,
  Moon,
  Settings,
  ShieldCheck,
  Sun,
  Upload as UploadIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Toaster } from '@/components/ui/sonner'
import { ApiError, api, type AuthStatus, type Health, type Vocabulary } from '@/lib/api'
import { href, navigate, useRoute } from '@/lib/router'
import { useTheme, type Theme } from '@/lib/theme'
import { cn } from '@/lib/utils'
import { VocabularyContext } from '@/lib/vocabulary'
import { Admin } from '@/pages/Admin'
import { Home } from '@/pages/Home'
import { Login } from '@/pages/Login'
import { Queue } from '@/pages/Queue'
import { Review } from '@/pages/Review'
import { Roles } from '@/pages/Roles'
import { Upload } from '@/pages/Upload'

const THEMES: { value: Theme; icon: typeof Sun; label: string }[] = [
  { value: 'system', icon: Monitor, label: 'Theme: follows the system' },
  { value: 'light', icon: Sun, label: 'Theme: light' },
  { value: 'dark', icon: Moon, label: 'Theme: dark' },
]

export default function App() {
  const route = useRoute()
  const [theme, setTheme] = useTheme()
  const [auth, setAuth] = useState<AuthStatus | null>(null)
  const [vocabulary, setVocabulary] = useState<Vocabulary | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    api.auth.status().then(setAuth).catch(() => setOffline(true))
  }, [])

  // Everything past the home and login pages needs the session; loaded once it exists.
  const user = auth?.user ?? null
  useEffect(() => {
    if (!user) return
    const signedOut = (failure: unknown) => {
      if (failure instanceof ApiError && failure.status === 401) setAuth({ setup_required: false, user: null })
      else setOffline(true)
    }
    api.vocabulary().then(setVocabulary).catch(signedOut)
    api.health().then(setHealth).catch(signedOut)
  }, [user])

  const signOut = async () => {
    await api.auth.logout().catch(() => undefined)
    setAuth({ setup_required: false, user: null })
    setVocabulary(null)
    navigate({ page: 'home' })
  }

  const cycleTheme = () => {
    const index = THEMES.findIndex((entry) => entry.value === theme)
    setTheme(THEMES[(index + 1) % THEMES.length].value)
  }
  const themeEntry = THEMES.find((entry) => entry.value === theme) ?? THEMES[0]

  const links = [
    { route: { page: 'home' } as const, label: 'Home', icon: HomeIcon, open: true },
    { route: { page: 'queue' } as const, label: 'Queue', icon: Inbox, open: false },
    { route: { page: 'upload' } as const, label: 'Upload', icon: UploadIcon, open: false },
    { route: { page: 'roles' } as const, label: 'Roles', icon: BookOpenCheck, open: false },
    { route: { page: 'admin' } as const, label: 'Settings', icon: Settings, open: false },
  ]

  const guarded = route.page !== 'home' && route.page !== 'login'
  const showLogin = !offline && auth !== null && !auth.user && (route.page === 'login' || guarded)

  return (
    <VocabularyContext.Provider value={vocabulary}>
      <div className="page-wash min-h-dvh">
        <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
            <a href={href({ page: 'home' })} className="mr-2 inline-flex items-center gap-2 font-heading font-semibold">
              <span className="inline-flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                <ShieldCheck className="size-4" aria-hidden="true" />
              </span>
              Submission Desk
            </a>
            <nav aria-label="Main" className="flex flex-wrap gap-1">
              {links
                .filter((link) => link.open || user)
                .map(({ route: target, label, icon: Icon }) => {
                  const active = route.page === target.page
                  return (
                    <a
                      key={label}
                      href={href(target)}
                      aria-current={active ? 'page' : undefined}
                      className={cn(
                        'inline-flex min-h-10 items-center gap-2 rounded-md px-3 text-sm font-medium transition-colors',
                        active
                          ? 'bg-secondary text-secondary-foreground'
                          : 'text-muted-foreground hover:bg-secondary/60 hover:text-foreground',
                      )}
                    >
                      <Icon className="size-4" aria-hidden="true" />
                      {label}
                    </a>
                  )
                })}
            </nav>
            <div className="ml-auto flex items-center gap-2">
              {health?.demo_mode && (
                <span
                  className="hidden items-center gap-1.5 rounded-md border border-warning/40 bg-warning/10 px-2 py-1 text-xs sm:inline-flex"
                  title="Demo mode: synthetic candidates, nothing is sent"
                >
                  <FlaskConical className="size-3.5" aria-hidden="true" />
                  Demo mode
                </span>
              )}
              <Button type="button" variant="ghost" size="icon" onClick={cycleTheme} aria-label={themeEntry.label} title={themeEntry.label}>
                <themeEntry.icon aria-hidden="true" />
              </Button>
              {user ? (
                <>
                  <span className="hidden text-sm text-muted-foreground sm:inline">{user}</span>
                  <Button type="button" variant="ghost" size="sm" onClick={signOut}>
                    <LogOut aria-hidden="true" />
                    Sign out
                  </Button>
                </>
              ) : (
                <Button asChild size="sm">
                  <a href={href({ page: 'login' })}>
                    <LogIn aria-hidden="true" />
                    Sign in
                  </a>
                </Button>
              )}
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-4 py-8">
          {offline ? (
            <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-4">
              The API is not reachable. Start it with <code>make api</code> and reload.
            </p>
          ) : route.page === 'home' ? (
            <Home user={user} />
          ) : auth === null ? null : showLogin ? (
            <Login
              setupRequired={auth.setup_required}
              onSignedIn={(name) => {
                setAuth({ setup_required: false, user: name })
                if (route.page === 'login') navigate({ page: 'queue' })
              }}
            />
          ) : route.page === 'login' ? (
            <Queue />
          ) : route.page === 'review' ? (
            // Keyed by run, so opening another candidate remounts the page: fresh
            // state, fresh clock.
            <Review key={route.runId} runId={route.runId} health={health} />
          ) : route.page === 'upload' ? (
            <Upload health={health} />
          ) : route.page === 'roles' ? (
            <Roles key={route.roleId ?? ''} roleId={route.roleId} />
          ) : route.page === 'admin' ? (
            <Admin />
          ) : (
            <Queue />
          )}
        </main>

        <footer className="mx-auto max-w-6xl px-4 pb-8 text-xs text-muted-foreground">
          Submission Desk · every quotation shown was checked against its source document.
        </footer>
      </div>
      <Toaster position="bottom-right" />
    </VocabularyContext.Provider>
  )
}
