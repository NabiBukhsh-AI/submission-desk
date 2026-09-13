import { CircleHelp, ListTree, TriangleAlert } from 'lucide-react'
import { useVocabulary } from '@/lib/vocabulary'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import type { Assessment, Recommendation, Rubric, RunRow } from '@/lib/api'

// The outcome, with every word on it defined. Numbers come from the run and
// the derivation; sentences come from the API's vocabulary, never from here.

const KIND_LABEL: Record<string, string> = {
  standard: 'Standard',
  high_stakes: 'High stakes',
  blocker: 'Blocker',
}

// The derivation records "score=0.7500" even when no band is reported, so the
// provisional figure can be shown next to the reason it was withheld.
function numberFrom(output: string | undefined, key: string): number | null {
  const match = output?.match(new RegExp(`${key}=([0-9.]+)`))
  return match ? Number(match[1]) : null
}

function money(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return 'not configured'
  const amount = Number(value)
  return Number.isFinite(amount) ? `$${amount.toFixed(4)}` : value
}

export function Outcome({
  run,
  recommendation,
  rubric,
  assessments,
}: {
  run: RunRow
  recommendation: Recommendation
  rubric: Rubric
  assessments: Assessment[]
}) {
  const vocabulary = useVocabulary()
  const bandLabel = vocabulary?.bands[recommendation.band] ?? recommendation.band
  const bandHelp = vocabulary?.band_help[recommendation.band]
  const gated = recommendation.score === null
  const byCriterion = Object.fromEntries(assessments.map((item) => [item.criterion_id, item]))

  const coverageStep = recommendation.derivation.find((step) => step.rule_id === 'R-COVERAGE')
  const scoreStep = recommendation.derivation.find((step) => step.rule_id === 'R-SCORE')
  const coverage = run.coverage ?? recommendation.coverage ?? numberFrom(coverageStep?.output, 'coverage')
  const provisional = recommendation.score ?? numberFrom(scoreStep?.output, 'score')
  const assessed = Object.values(recommendation.criterion_states).filter(
    (state) => state !== 'insufficient_evidence',
  ).length
  const verified = assessments.reduce(
    (total, item) => total + item.evidence.filter((piece) => piece.verbatim_span).length,
    0,
  )
  const excluded = assessments.reduce((total, item) => total + item.rejected_evidence.length, 0)

  const facts: [string, string, string][] = [
    [
      'Coverage',
      coverage === null ? '—' : `${Math.round(coverage * 100)}%`,
      `Share of the rubric, by weight, the documents address. This role needs ${Math.round(
        rubric.min_coverage * 100,
      )}% before a score is reported.`,
    ],
    [
      gated ? 'Provisional score' : 'Score',
      provisional === null ? '—' : provisional.toFixed(2),
      gated
        ? 'Computed over the requirements that could be assessed, but not reported as a band — see the reasons below.'
        : 'Weighted average over the requirements that could be assessed; unaddressed ones are excluded, not zero.',
    ],
    [
      'Requirements assessed',
      `${assessed} of ${rubric.criteria.length}`,
      'Requirements the documents said something about. The rest are "not addressed" and count for nothing either way.',
    ],
    [
      'Verified quotations',
      String(verified),
      'Quotations the model offered that were found in the document. Only these count.',
    ],
    [
      'Excluded quotations',
      String(excluded),
      'Quotations that were not in the document, removed before scoring and listed below.',
    ],
    [
      'Cost',
      money(run.total_cost_usd),
      `${run.llm_call_count} model call${run.llm_call_count === 1 ? '' : 's'}, ${run.total_input_tokens.toLocaleString()} tokens in, ${run.total_output_tokens.toLocaleString()} out, as the provider reported.`,
    ],
    [
      'Repairs · escalations',
      `${run.repair_count} · ${run.escalation_count}`,
      'Repairs: answers that failed the contract once and were asked for again. Escalations: requirements checked a second time by the stronger tier.',
    ],
  ]

  return (
    <section aria-labelledby="outcome-heading" className="rounded-xl border bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Outcome</p>
          <h2 id="outcome-heading" className="mt-1 text-2xl font-semibold">
            {bandLabel}
          </h2>
          {bandHelp && <p className="mt-2 max-w-3xl text-sm text-muted-foreground">{bandHelp}</p>}
        </div>
        {!gated && provisional !== null && (
          <div className="w-full sm:w-56">
            <Progress value={Math.min(Math.max(provisional, 0), 1) * 100} aria-label="Score" />
            <p className="mt-1 text-right text-xs text-muted-foreground">score {provisional.toFixed(2)} of 1.00</p>
          </div>
        )}
      </div>

      <dl className="mt-5 grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
        {facts.map(([label, value, help]) => (
          <div key={label}>
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="font-heading text-lg font-semibold">{value}</dd>
            <dd className="mt-0.5 text-xs text-muted-foreground">{help}</dd>
          </div>
        ))}
      </dl>

      {recommendation.requires_human && (
        <Alert className="mt-5 border-warning/50 bg-warning/10">
          <TriangleAlert className="text-warning" aria-hidden="true" />
          <AlertTitle>Why this needs a person</AlertTitle>
          <AlertDescription className="w-full">
            <p className="text-foreground/80">
              None of these change the band. They are reasons to check the work, not reasons to think less of the
              candidate.
            </p>
            <ul className="mt-3 space-y-3">
              {recommendation.requires_human_reasons.map((code) => {
                const reason = vocabulary?.human_reasons[code]
                return (
                  <li key={code} className="rounded-md border bg-card p-3 text-foreground">
                    <p className="font-medium">{reason?.title ?? code}</p>
                    {reason && (
                      <>
                        <p className="mt-1 text-sm text-muted-foreground">{reason.meaning}</p>
                        <p className="mt-1.5 text-sm">
                          <span className="font-medium">What to do: </span>
                          {reason.action}
                        </p>
                      </>
                    )}
                    <p className="mt-1.5 font-mono text-[11px] text-muted-foreground">{code}</p>
                  </li>
                )
              })}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <Accordion type="multiple" className="mt-5" defaultValue={['derivation']}>
        <AccordionItem value="derivation">
          <AccordionTrigger>
            <span className="inline-flex items-center gap-2">
              <ListTree className="size-4" aria-hidden="true" />
              How this was worked out
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <p className="mb-3 text-sm text-muted-foreground">
              The rules that fired, in order. Each is a fixed rule in the code, not a model's judgement; the band can
              be traced through them by hand.
            </p>
            <ol className="space-y-3">
              {recommendation.derivation.map((step, index) => (
                <li key={index} className="rounded-md border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-heading text-xs text-muted-foreground">{index + 1}</span>
                    <Badge variant="outline" className="font-mono text-[11px]">
                      {step.rule_id}
                    </Badge>
                    {step.output && <span className="font-mono text-[11px] text-muted-foreground">→ {step.output}</span>}
                  </div>
                  <p className="mt-1.5 text-sm">{step.description}</p>
                  {step.inputs && Object.keys(step.inputs).length > 0 && (
                    <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                      {Object.entries(step.inputs)
                        .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
                        .join('  ')}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="points">
          <AccordionTrigger>Point by point</AccordionTrigger>
          <AccordionContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="py-1.5 pr-3 font-medium">Requirement</th>
                    <th className="py-1.5 pr-3 font-medium">Kind</th>
                    <th className="py-1.5 pr-3 font-medium">Weight</th>
                    <th className="py-1.5 pr-3 font-medium">Quotations</th>
                    <th className="py-1.5 pr-3 font-medium">State</th>
                    <th className="py-1.5 font-medium">In coverage</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {rubric.criteria.map((criterion) => {
                    const state = recommendation.criterion_states[criterion.id]
                    const assessment = byCriterion[criterion.id]
                    const found = assessment?.evidence.filter((piece) => piece.verbatim_span).length ?? 0
                    const counted = state !== undefined && state !== 'insufficient_evidence'
                    return (
                      <tr key={criterion.id} className="align-top">
                        <td className="py-2 pr-3">
                          <div className="font-medium">{criterion.label}</div>
                          {assessment?.unassessed_reason && (
                            <div className="text-xs text-warning">not assessed: {assessment.unassessed_reason.replaceAll('_', ' ')}</div>
                          )}
                        </td>
                        <td className="py-2 pr-3" title={vocabulary?.kinds[criterion.kind]}>
                          {KIND_LABEL[criterion.kind] ?? criterion.kind}
                        </td>
                        <td className="py-2 pr-3">{criterion.weight}</td>
                        <td className="py-2 pr-3" title="verified quotations found / needed for 'met'">
                          {found} / {criterion.min_supported}
                        </td>
                        <td className="py-2 pr-3" title={state ? vocabulary?.state_help[state] : undefined}>
                          {state ? (vocabulary?.states[state] ?? state) : '—'}
                        </td>
                        <td className="py-2">{counted ? 'yes' : 'no'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              Kind, weight and quotations needed come from the rubric ({rubric.role_title} v{rubric.version}). Hover
              a kind or a state for what it means; the full definitions are at the bottom of the page.
            </p>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </section>
  )
}

export function Glossary() {
  const vocabulary = useVocabulary()
  if (!vocabulary) return null
  const groups: [string, [string, string][]][] = [
    ['Outcomes', Object.entries(vocabulary.band_help).map(([key, help]) => [vocabulary.bands[key] ?? key, help])],
    ['States of a requirement', Object.entries(vocabulary.state_help).map(([key, help]) => [vocabulary.states[key] ?? key, help])],
    ['Kinds of requirement', Object.entries(vocabulary.kinds).map(([key, help]) => [KIND_LABEL[key] ?? key, help])],
    ['Checking a quotation', Object.entries(vocabulary.span_validation).map(([key, help]) => [key.replaceAll('_', ' '), help])],
    ['Other words on this page', vocabulary.glossary.map((entry) => [entry.term, entry.definition])],
  ]
  return (
    <section aria-labelledby="glossary-heading">
      <Accordion type="single" collapsible className="rounded-xl border bg-card px-4">
        <AccordionItem value="glossary">
          <AccordionTrigger>
            <span id="glossary-heading" className="inline-flex items-center gap-2">
              <CircleHelp className="size-4" aria-hidden="true" />
              What every word on this page means
            </span>
          </AccordionTrigger>
          <AccordionContent>
            <div className="grid gap-6 md:grid-cols-2">
              {groups.map(([title, entries]) => (
                <div key={title}>
                  <h3 className="text-sm font-semibold">{title}</h3>
                  <dl className="mt-2 space-y-2">
                    {entries.map(([term, definition]) => (
                      <div key={term}>
                        <dt className="text-sm font-medium">{term}</dt>
                        <dd className="text-sm text-muted-foreground">{definition}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </section>
  )
}
