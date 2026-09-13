import { useEffect, useState } from 'react'
import { CheckCircle2, Info, OctagonAlert, Trash2, TriangleAlert } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { navigate } from '@/lib/router'
import { useVocabulary } from '@/lib/vocabulary'
import { Decision } from '@/components/Decision'
import { Evidence } from '@/components/Evidence'
import { Glossary, Outcome } from '@/components/Outcome'
import { StatusChip } from '@/components/StatusChip'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { ApiError, api, type Detail, type Health, type Report } from '@/lib/api'

export function Review({ runId, health }: { runId: string; health: Health | null }) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Opening a candidate starts the clock. Recorded on the decision because it
  // is one of the two numbers a productivity claim would need. The page is
  // keyed by run id, so a new candidate is a new clock.
  const [openedAt] = useState(() => Date.now())

  useEffect(() => {
    api.run(runId).then(setDetail).catch((failure: Error) => setError(failure.message))
  }, [runId])

  if (error) {
    return (
      <p role="alert" className="text-destructive">
        {error}
      </p>
    )
  }
  if (!detail) {
    return (
      <p className="text-muted-foreground" aria-live="polite">
        Loading…
      </p>
    )
  }

  const { run, rubric, banner, recommendation, assessments, reports } = detail
  const blockers = rubric.criteria.filter((item) => item.kind === 'blocker').map((item) => item.id)

  const remove = async () => {
    if (!window.confirm(`Delete ${run.candidate_id} and everything recorded about this run? This cannot be undone.`)) return
    try {
      await api.deleteRun(run.run_id)
      toast.success(`${run.candidate_id} was deleted.`)
      navigate({ page: 'queue' })
    } catch (failure) {
      toast.error(failure instanceof ApiError ? failure.message : 'The run could not be deleted.', { duration: 8000 })
    }
  }

  return (
    <article className="space-y-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {run.candidate_id}
            {run.sample && (
              <Badge variant="secondary" className="ml-2 align-middle">
                sample
              </Badge>
            )}
          </h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span>{rubric.role_title}</span>
            <span aria-hidden="true">·</span>
            <StatusChip status={run.status} chip={run.chip} />
            <span aria-hidden="true">·</span>
            <span>run {run.run_id.slice(0, 8)}</span>
          </p>
        </div>
        {!run.sample && (
          <Button type="button" variant="outline" size="sm" onClick={remove}>
            <Trash2 aria-hidden="true" />
            Delete
          </Button>
        )}
      </header>

      <IntegrityBanner tier={banner.tier} message={banner.message} reports={reports} />

      {recommendation ? (
        <Outcome run={run} recommendation={recommendation} rubric={rubric} assessments={assessments} />
      ) : (
        <Alert>
          <TriangleAlert aria-hidden="true" />
          <AlertTitle>No recommendation was produced for this candidate.</AlertTitle>
          <AlertDescription>The documents are below; you can still read them and decide.</AlertDescription>
        </Alert>
      )}

      {rubric.notes_for_reviewer && (
        <Alert>
          <Info aria-hidden="true" />
          <AlertDescription>{rubric.notes_for_reviewer}</AlertDescription>
        </Alert>
      )}

      <Evidence runId={runId} assessments={assessments} rubric={rubric} blockers={blockers} />

      <section aria-labelledby="ask-heading">
        <h2 id="ask-heading" className="text-lg font-semibold">
          What to ask next
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Derived from the points the documents did not settle. Suggested questions are prepared
          when the candidate is processed and are sent only if you approve.
        </p>
      </section>

      <Decision
        run={run}
        rubric={rubric}
        assessments={assessments}
        recommendation={recommendation}
        health={health}
        openedAt={openedAt}
      />

      <Glossary />
    </article>
  )
}

// Never suppressed, never softened. The wording comes from the API, which
// takes it from domain.security, so no client can invent a gentler red one.
function IntegrityBanner({ tier, message, reports }: { tier: string; message: string; reports: Report[] }) {
  const vocabulary = useVocabulary()
  const findings = reports.flatMap((report) => report.findings)

  if (tier === 'clean') {
    return (
      <Alert className="border-success/40 bg-success/10">
        <CheckCircle2 className="text-success" aria-hidden="true" />
        <AlertTitle>{message}</AlertTitle>
      </Alert>
    )
  }

  const quarantine = tier === 'quarantine'
  return (
    <Alert
      variant={quarantine ? 'destructive' : 'default'}
      className={quarantine ? '' : 'border-warning/50 bg-warning/10'}
    >
      {quarantine ? <OctagonAlert aria-hidden="true" /> : <TriangleAlert className="text-warning" aria-hidden="true" />}
      <AlertTitle>{message}</AlertTitle>
      {findings.length > 0 && (
        <AlertDescription className="mt-2 w-full">
          <p className="font-medium text-foreground">What was found</p>
          <ul className="mt-2 space-y-3">
            {findings.map((finding, index) => (
              <li key={index} className="rounded-md border bg-card p-3 text-foreground">
                <p className="text-sm">
                  <strong>{vocabulary?.severities[finding.severity] ?? finding.severity}</strong> —{' '}
                  {vocabulary?.detectors[finding.detector] ?? finding.detector}
                </p>
                {/* Quoted in full, not summarised: deciding whether this was an
                    attack or an accident means reading the words. */}
                <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded bg-muted p-2 font-mono text-xs">
                  {finding.excerpt}
                </pre>
                <p className="mt-1 text-xs text-muted-foreground">Detector {finding.detector}</p>
              </li>
            ))}
          </ul>
        </AlertDescription>
      )}
    </Alert>
  )
}
