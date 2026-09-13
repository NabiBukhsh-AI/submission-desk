import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { api, type SentRow } from '@/lib/api'
import { href } from '@/lib/router'
import { cn } from '@/lib/utils'

const REFRESH_MS = 5000

// The spreadsheet's table, on the page: the same columns every destination
// was given, with a cell per destination saying whether it arrived and where.
// A recruiter who does not open the sheet still sees what was sent.
const SINK_NAMES: Record<string, string> = { csv: 'CSV', sheets: 'Google Sheets' }

export function SentTable() {
  const [rows, setRows] = useState<SentRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      api
        .sent()
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
  }, [])

  if (error) {
    return (
      <p role="alert" className="mt-4 text-destructive">
        {error}
      </p>
    )
  }
  if (rows === null) {
    return (
      <p className="mt-6 text-muted-foreground" aria-live="polite">
        Loading…
      </p>
    )
  }
  if (rows.length === 0) {
    return (
      <div className="mt-6 rounded-xl border border-dashed p-10 text-center text-muted-foreground">
        Nothing has been sent yet. Approve a candidate, then press <strong>Send approved</strong>.
      </div>
    )
  }

  const sinks = [...new Set(rows.flatMap((row) => row.destinations.map((d) => d.sink_id)))]

  return (
    <div className="mt-3 overflow-x-auto rounded-xl border bg-card">
      <table className="w-full min-w-[960px] text-sm">
        <caption className="sr-only">Candidates sent to the destinations</caption>
        <thead className="bg-secondary/60 text-left text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th scope="col" className="p-3 font-medium">
              Candidate
            </th>
            <th scope="col" className="p-3 font-medium">
              Role
            </th>
            <th scope="col" className="p-3 font-medium">
              Outcome
            </th>
            <th scope="col" className="p-3 text-right font-medium">
              Score
            </th>
            <th scope="col" className="p-3 text-right font-medium">
              Coverage
            </th>
            <th scope="col" className="p-3 font-medium">
              Decision
            </th>
            <th scope="col" className="p-3 font-medium">
              Reasoning
            </th>
            {sinks.map((sink) => (
              <th key={sink} scope="col" className="p-3 font-medium">
                {SINK_NAMES[sink] ?? sink}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((row) => (
            <tr key={row.run_id} className="align-top hover:bg-secondary/40">
              <td className="p-3">
                <a href={href({ page: 'review', runId: row.run_id })} className="font-medium underline-offset-2 hover:underline">
                  {row.candidate_id}
                </a>
                <div className="mt-1 text-xs text-muted-foreground">
                  {row.integrity !== 'clean' && <Badge variant="outline">{row.integrity}</Badge>}
                </div>
              </td>
              <td className="p-3">
                <Badge variant="outline">{row.role_id}</Badge>
              </td>
              <td className="p-3 font-medium">{row.band_label}</td>
              <td className="p-3 text-right tabular-nums">{row.score === null ? '—' : row.score.toFixed(2)}</td>
              <td className="p-3 text-right tabular-nums">{Math.round(row.coverage * 100)}%</td>
              <td className="p-3">
                <div>{row.decision.replace(/_/g, ' ')}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {row.reviewer_id}
                  {row.decided_at && ` · ${new Date(row.decided_at).toLocaleString()}`}
                  {row.corrections > 0 && ` · ${row.corrections} correction${row.corrections === 1 ? '' : 's'}`}
                </div>
              </td>
              <td className="max-w-md p-3">
                <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
                  {row.reasoning.map((line, index) => (
                    <li key={index}>{line}</li>
                  ))}
                </ul>
              </td>
              {sinks.map((sink) => {
                const destination = row.destinations.find((d) => d.sink_id === sink)
                return (
                  <td key={sink} className="p-3 text-xs">
                    {destination ? (
                      <>
                        <span
                          className={cn(
                            'font-medium',
                            destination.status === 'delivered' ? 'text-emerald-700 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-400',
                          )}
                        >
                          {destination.status === 'delivered' ? 'Sent' : destination.status === 'pending_retry' ? 'Will retry' : 'Failed'}
                        </span>
                        <div className="mt-1 break-all text-muted-foreground">{destination.reference || destination.error}</div>
                      </>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
