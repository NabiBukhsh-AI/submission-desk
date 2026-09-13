import { useEffect, useState } from 'react'
import { FlaskConical, Inbox, LogOut, Settings, Upload as UploadIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Toaster } from '@/components/ui/sonner'
import { ApiError, api, type AuthStatus, type Health, type Vocabulary } from '@/lib/api'
import { href, navigate, useRoute } from '@/lib/router'
import { cn } from '@/lib/utils'
import { VocabularyContext } from '@/lib/vocabulary'
import { Admin } from '@/pages/Admin'
import { Login } from '@/pages/Login'
import { Queue } from '@/pages/Queue'
import { Review } from '@/pages/Review'
import { Upload } from '@/pages/Upload'

export default function App() {
  const route = useRoute()
  const [auth, setAuth] = useState<AuthStatus | null>(null)
  const [vocabulary, setVocabulary] = useState<Vocabulary | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    api.auth.status().then(setAuth).catch(() => setOffline(true))
  }, [])

  // Everything past the login page needs the session; loaded once it exists.
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
    navigate({ page: 'queue' })
  }

  if (offline) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-6">
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-4">
          The API is not reachable. Start it with <code>make api</code> and reload.
        </p>
      </main>
    )
  }
  if (!auth) return null
  if (!auth.user) {
    return (
      <main className="px-4">
        <Login setupRequired={auth.setup_required} onSignedIn={(name) => setAuth({ setup_required: false, user: name })} />
        <Toaster position="bottom-right" />
      </main>
    )
  }

  const links = [
    { route: { page: 'queue' } as const, label: 'Queue', icon: Inbox },
    { route: { page: 'upload' } as const, label: 'Upload', icon: UploadIcon },
    { route: { page: 'admin' } as const, label: 'Settings', icon: Settings },
  ]

  return (
    <VocabularyContext.Provider value={vocabulary}>
      <div className="min-h-dvh">
        <header className="border-b bg-card">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
            <a href="#/queue" className="font-semibold tracking-tight">
              Submission Desk
            </a>
            <nav aria-label="Main" className="flex gap-1">
              {links.map(({ route: target, label, icon: Icon }) => {
                const active = route.page === target.page
                return (
                  <a
                    key={label}
                    href={href(target)}
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'inline-flex min-h-11 items-center gap-2 rounded-md px-3 text-sm transition-colors',
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
            <div className="ml-auto flex items-center gap-3">
              {health?.demo_mode && (
                <span className="inline-flex items-center gap-1.5 rounded-md border border-warning/40 bg-warning/10 px-2 py-1 text-xs">
                  <FlaskConical className="size-3.5" aria-hidden="true" />
                  Demo mode: synthetic candidates, nothing is sent
                </span>
              )}
              <span className="text-sm text-muted-foreground">{auth.user}</span>
              <Button type="button" variant="ghost" size="sm" onClick={signOut}>
                <LogOut aria-hidden="true" />
                Sign out
              </Button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-5xl px-4 py-6">
          {route.page === 'review' ? (
            // Keyed by run, so opening another candidate remounts the page: fresh
            // state, fresh clock.
            <Review key={route.runId} runId={route.runId} health={health} />
          ) : route.page === 'upload' ? (
            <Upload health={health} />
          ) : route.page === 'admin' ? (
            <Admin />
          ) : (
            <Queue />
          )}
        </main>
      </div>
      <Toaster position="bottom-right" />
    </VocabularyContext.Provider>
  )
}
