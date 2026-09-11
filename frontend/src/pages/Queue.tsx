import { useEffect, useState } from 'react'
import { useVocabulary } from '@/lib/vocabulary'
import { StatusChip } from '@/components/StatusChip'
import { Button } from '@/components/ui/button'
import { api, type RunRow } from '@/lib/api'
import { href } from '@/lib/router'
import { cn } from '@/lib/utils'

const DEFAULT_FILTER = 'Needs attention'
const REFRESH_MS = 5000

// Read from the API on every render and refreshed while processing is under
// way, so two people looking at the queue see the same thing and a refresh
// loses nothing.
export function Queue() {
  const vocabulary = useVocabulary()
  const [filter, setFilter] = useState(DEFAULT_FILTER)
  const [rows, setRows] = useState<RunRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      api
        .runs(filter)
        .then((data) => {
          if (!cancelled) {
            setRows(data)
            setError(null)
          }
        })
        .catch((failure: Error) => !cancelled && setError(failure.message))
    load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [filter])

  const filters = vocabulary ? Object.keys(vocabulary.filters) : [DEFAULT_FILTER]

  return (
    <section aria-labelledby="queue-heading">
      <h1 id="queue-heading" className="text-2xl font-semibold tracking-tight">
        Queue
      </h1>

      <div role="group" aria-label="Show" className="mt-4 flex flex-wrap gap-2">
        {filters.map((name) => (
          <Button
            key={name}
            size="sm"
            variant={name === filter ? 'default' : 'outline'}
            aria-pressed={name === filter}
            onClick={() => setFilter(name)}
          >
            {name}
          </Button>
        ))}
      </div>

      {error && (
        <p role="alert" className="mt-4 text-destructive">
          {error}
        </p>
      )}

      {rows === null ? (
        <p className="mt-6 text-muted-foreground" aria-live="polite">
          Loading…
        </p>
      ) : rows.length === 0 ? (
        <p className="mt-6 text-muted-foreground">
          {vocabulary?.empty[filter] ?? 'Nothing here.'}
        </p>
      ) : (
        <>
          <p className="mt-4 text-sm text-muted-foreground" role="status" aria-atomic="true">
            {rows.length} candidate{rows.length === 1 ? '' : 's'}
          </p>
          <ul className="mt-2 divide-y rounded-lg border bg-card">
            {rows.map((run) => (
              <li
                key={run.run_id}
                className="grid grid-cols-1 gap-2 p-4 sm:grid-cols-[1.5fr_1.3fr_1fr_auto] sm:items-center"
              >
                <div>
                  <div className="font-medium">{run.candidate_id}</div>
                  <div className="text-xs text-muted-foreground">
                    {run.role_id} · started {new Date(run.started_at).toLocaleString()}
                  </div>
                </div>
                <div>
                  <StatusChip status={run.status} chip={run.chip} tier={run.integrity_tier} />
                  {run.chip.help && (
                    <div className="mt-1 text-xs text-muted-foreground">{run.chip.help}</div>
                  )}
                </div>
                <div>
                  <div className={cn(run.final_band ? '' : 'text-muted-foreground')}>
                    {run.band_label}
                  </div>
                  {run.override_count > 0 && (
                    <div className="text-xs text-muted-foreground">
                      {run.override_count} correction{run.override_count === 1 ? '' : 's'} by a
                      reviewer
                    </div>
                  )}
                </div>
                <div className="sm:text-right">
                  <Button asChild size="sm" variant={run.chip.needs_attention ? 'default' : 'outline'}>
                    <a href={href({ page: 'review', runId: run.run_id })}>Open</a>
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
