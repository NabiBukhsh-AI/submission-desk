import { useEffect, useState } from 'react'
import { FlaskConical, Inbox, Upload as UploadIcon } from 'lucide-react'
import { Toaster } from '@/components/ui/sonner'
import { api, type Health, type Vocabulary } from '@/lib/api'
import { href, useRoute } from '@/lib/router'
import { cn } from '@/lib/utils'
import { VocabularyContext } from '@/lib/vocabulary'
import { Queue } from '@/pages/Queue'
import { Review } from '@/pages/Review'
import { Upload } from '@/pages/Upload'

export default function App() {
  const route = useRoute()
  const [vocabulary, setVocabulary] = useState<Vocabulary | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    api.vocabulary().then(setVocabulary).catch(() => setOffline(true))
    api.health().then(setHealth).catch(() => setOffline(true))
  }, [])

  const links = [
    { route: { page: 'queue' } as const, label: 'Queue', icon: Inbox },
    { route: { page: 'upload' } as const, label: 'Upload', icon: UploadIcon },
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
            {health?.demo_mode && (
              <span className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-warning/40 bg-warning/10 px-2 py-1 text-xs">
                <FlaskConical className="size-3.5" aria-hidden="true" />
                Demo mode: synthetic candidates, nothing is sent
              </span>
            )}
          </div>
        </header>

        <main className="mx-auto max-w-5xl px-4 py-6">
          {offline ? (
            <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-4">
              The API is not reachable. Start it with <code>make api</code> and reload.
            </p>
          ) : route.page === 'review' ? (
            // Keyed by run, so opening another candidate remounts the page: fresh
            // state, fresh clock.
            <Review key={route.runId} runId={route.runId} health={health} />
          ) : route.page === 'upload' ? (
            <Upload health={health} />
          ) : (
            <Queue />
          )}
        </main>
      </div>
      <Toaster position="bottom-right" />
    </VocabularyContext.Provider>
  )
}
