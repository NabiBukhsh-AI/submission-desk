import { useEffect, useState } from 'react'
import { Play } from 'lucide-react'
import { toast } from 'sonner'
import { useVocabulary } from '@/lib/vocabulary'
import { RolePicker } from '@/components/RolePicker'
import { StatusChip } from '@/components/StatusChip'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ApiError, api, type RunRow } from '@/lib/api'
import { useRoles } from '@/lib/roles'
import { href } from '@/lib/router'
import { cn } from '@/lib/utils'

const DEFAULT_FILTER = 'Needs attention'
const REFRESH_MS = 5000

// Read from the API on every render and refreshed while processing is under
// way, so two people looking at the queue see the same thing and a refresh
// loses nothing. Rows can be selected and sent through again against another
// role: the stored documents are used, so nothing is uploaded twice.
export function Queue() {
  const vocabulary = useVocabulary()
  const roles = useRoles()
  const [filter, setFilter] = useState(DEFAULT_FILTER)
  const [rows, setRows] = useState<RunRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [chosenRole, setTargetRole] = useState('')
  const [busy, setBusy] = useState(false)
  // The first role until one is chosen; derived, not stored.
  const targetRole = chosenRole || roles?.[0]?.role_id || ''

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
  const visible = rows ?? []
  const allChosen = visible.length > 0 && visible.every((run) => selected.has(run.run_id))

  const toggle = (runId: string) =>
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(runId)) next.delete(runId)
      else next.add(runId)
      return next
    })
  const toggleAll = () => setSelected(allChosen ? new Set() : new Set(visible.map((run) => run.run_id)))

  const runSelected = async () => {
    if (selected.size === 0 || !targetRole) return
    setBusy(true)
    try {
      const result = await api.reassess([...selected], targetRole)
      const title = roles?.find((role) => role.role_id === targetRole)?.role_title ?? targetRole
      toast.success(
        `${result.accepted} candidate${result.accepted === 1 ? '' : 's'} sent through against ${title}. Progress shows here.`,
      )
      setSelected(new Set())
      setFilter('In progress')
    } catch (failure) {
      toast.error(failure instanceof ApiError ? failure.message : 'The candidates could not be sent.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="queue-heading">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 id="queue-heading" className="text-2xl font-semibold">
            Queue
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Every candidate the system has read, by what is waiting on you.
          </p>
        </div>
        <div role="group" aria-label="Show" className="flex flex-wrap gap-2">
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
        <div className="mt-6 rounded-xl border border-dashed p-10 text-center text-muted-foreground">
          {vocabulary?.empty[filter] ?? 'Nothing here.'}
        </div>
      ) : (
        <>
          <div className="mt-5 flex flex-wrap items-end gap-3 rounded-xl border bg-card p-3">
            <label className="inline-flex min-h-10 items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={allChosen}
                onChange={toggleAll}
                aria-label="Select every candidate shown"
              />
              {selected.size === 0
                ? `${rows.length} candidate${rows.length === 1 ? '' : 's'}`
                : `${selected.size} selected`}
            </label>
            <RolePicker
              id="target-role"
              roles={roles}
              value={targetRole}
              onChange={setTargetRole}
              label="Run the selected candidates against"
              className="min-w-64"
            />
            <Button type="button" onClick={runSelected} disabled={busy || selected.size === 0 || !targetRole}>
              <Play aria-hidden="true" />
              Run {selected.size > 0 ? selected.size : ''} on this role
            </Button>
            <span className="text-xs text-muted-foreground">
              Uses the documents already stored. The same documents on the same role are recognised, not repeated.
            </span>
          </div>

          <ul className="mt-3 divide-y overflow-hidden rounded-xl border bg-card">
            {rows.map((run) => (
              <li
                key={run.run_id}
                className={cn(
                  'grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-2 p-4 transition-colors hover:bg-secondary/40 sm:grid-cols-[auto_1.5fr_1.3fr_1fr_auto]',
                  selected.has(run.run_id) && 'bg-primary/5',
                )}
              >
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={selected.has(run.run_id)}
                  onChange={() => toggle(run.run_id)}
                  aria-label={`Select ${run.candidate_id}`}
                />
                <div>
                  <div className="font-medium">{run.candidate_id}</div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <Badge variant="outline">{run.role_id}</Badge>
                    started {new Date(run.started_at).toLocaleString()}
                  </div>
                </div>
                <div className="col-start-2 sm:col-start-auto">
                  <StatusChip status={run.status} chip={run.chip} tier={run.integrity_tier} />
                  {run.chip.help && <div className="mt-1 text-xs text-muted-foreground">{run.chip.help}</div>}
                </div>
                <div className="col-start-2 sm:col-start-auto">
                  <div className={cn(run.final_band ? 'font-medium' : 'text-muted-foreground')}>{run.band_label}</div>
                  {run.override_count > 0 && (
                    <div className="text-xs text-muted-foreground">
                      {run.override_count} correction{run.override_count === 1 ? '' : 's'} by a reviewer
                    </div>
                  )}
                </div>
                <div className="col-start-2 sm:col-start-auto sm:text-right">
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
