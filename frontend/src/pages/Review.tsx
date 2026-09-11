import { useEffect, useState } from 'react'
import { CheckCircle2, Info, OctagonAlert, TriangleAlert } from 'lucide-react'
import { useVocabulary } from '@/lib/vocabulary'
import { Decision } from '@/components/Decision'
import { Evidence } from '@/components/Evidence'
import { StatusChip } from '@/components/StatusChip'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Progress } from '@/components/ui/progress'
import { api, type Detail, type Health, type Recommendation, type Report } from '@/lib/api'

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

  return (
    <article className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{run.candidate_id}</h1>
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
          <span>{rubric.role_title}</span>
          <span aria-hidden="true">·</span>
          <StatusChip status={run.status} chip={run.chip} />
          <span aria-hidden="true">·</span>
          <span>run {run.run_id.slice(0, 8)}</span>
        </p>
      </header>

      <IntegrityBanner tier={banner.tier} message={banner.message} reports={reports} />

      {recommendation ? (
        <RecommendationPanel recommendation={recommendation} rubric={rubric} />
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

function RecommendationPanel({ recommendation, rubric }: { recommendation: Recommendation; rubric: Detail['rubric'] }) {
  const vocabulary = useVocabulary()
  const bandLabel = vocabulary?.bands[recommendation.band] ?? recommendation.band
  const insufficient = recommendation.band === 'insufficient_information'
  const byId = Object.fromEntries(rubric.criteria.map((item) => [item.id, item]))

  return (
    <section aria-labelledby="recommendation-heading" className="rounded-lg border bg-card p-5">
      {insufficient ? (
        <Alert className="border-warning/50 bg-warning/10">
          <TriangleAlert className="text-warning" aria-hidden="true" />
          <AlertTitle id="recommendation-heading">
            {bandLabel} — the documents did not cover enough of this role to score.
          </AlertTitle>
        </Alert>
      ) : (
        <h2 id="recommendation-heading" className="text-xl font-semibold">
          {bandLabel}
        </h2>
      )}

      {recommendation.score !== null ? (
        <div className="mt-3">
          <Progress value={Math.min(Math.max(recommendation.score, 0), 1) * 100} aria-label="Score" />
          <p className="mt-1 text-sm text-muted-foreground">
            Score {recommendation.score.toFixed(2)} of 1.00
          </p>
        </div>
      ) : (
        // Never a zero. A zero reads as "scored badly"; the honest statement
        // is that no score was produced.
        <p className="mt-3 text-sm text-muted-foreground">
          No score was produced, because there was not enough to judge.
        </p>
      )}

      {recommendation.requires_human && (
        <Alert className="mt-4">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle>This one wants a person's judgement.</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {recommendation.requires_human_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <h3 className="mt-5 font-medium">How this was worked out</h3>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">
        {recommendation.derivation.map((step, index) => (
          <li key={index}>{step.description}</li>
        ))}
      </ol>

      <Accordion type="single" collapsible className="mt-4">
        <AccordionItem value="states">
          <AccordionTrigger>Point by point</AccordionTrigger>
          <AccordionContent>
            <ul className="space-y-1 text-sm">
              {Object.entries(recommendation.criterion_states).map(([criterionId, state]) => {
                const criterion = byId[criterionId]
                return (
                  <li key={criterionId} className="flex flex-wrap justify-between gap-2">
                    <span>
                      <strong>{criterion?.label ?? criterionId}</strong>
                      {criterion && <span className="text-muted-foreground"> · weight {criterion.weight}</span>}
                    </span>
                    <span>{vocabulary?.states[state] ?? state}</span>
                  </li>
                )
              })}
            </ul>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </section>
  )
}
