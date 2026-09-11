import { useState } from 'react'
import { useVocabulary } from '@/lib/vocabulary'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { api, type Assessment, type EvidenceItem, type Passage, type Rubric } from '@/lib/api'

const EVIDENCE_STATE: Record<EvidenceItem['state'], string> = {
  supported: 'Supports',
  contradicted: 'Contradicts',
  insufficient_evidence: 'Not addressed',
}

export function Evidence({
  runId,
  assessments,
  rubric,
  blockers,
}: {
  runId: string
  assessments: Assessment[]
  rubric: Rubric
  blockers: string[]
}) {
  const vocabulary = useVocabulary()
  const byId = Object.fromEntries(rubric.criteria.map((item) => [item.id, item]))
  const rejected = assessments.flatMap((item) => item.rejected_evidence)
  const eligibility = assessments.filter((item) => blockers.includes(item.criterion_id))

  return (
    <>
      <section aria-labelledby="evidence-heading">
        <h2 id="evidence-heading" className="text-lg font-semibold">
          What the documents say
        </h2>
        <Accordion type="multiple" className="mt-2 rounded-lg border bg-card px-4">
          {assessments
            .filter((item) => !blockers.includes(item.criterion_id))
            .map((assessment) => {
              const criterion = byId[assessment.criterion_id]
              return (
                <AccordionItem key={assessment.criterion_id} value={assessment.criterion_id}>
                  <AccordionTrigger className="gap-3">
                    <span className="flex flex-1 flex-wrap items-center justify-between gap-2 text-left">
                      <span>{criterion?.label ?? assessment.criterion_id}</span>
                      <Badge variant="outline">
                        {vocabulary?.states[assessment.resolved_state] ?? assessment.resolved_state}
                      </Badge>
                    </span>
                  </AccordionTrigger>
                  <AccordionContent>
                    {criterion && <p className="mb-3 text-sm text-muted-foreground">{criterion.question}</p>}
                    {assessment.evidence.length === 0 && (
                      <p className="text-sm text-muted-foreground">
                        Nothing in the documents addressed this point.
                      </p>
                    )}
                    <ul className="space-y-3">
                      {assessment.evidence.map((item) => (
                        <EvidenceCard key={item.evidence_id} runId={runId} item={item} />
                      ))}
                    </ul>
                  </AccordionContent>
                </AccordionItem>
              )
            })}
        </Accordion>
      </section>

      {/* Prominent on purpose. This is the hallucination rate made visible: if it
          is empty the system found nothing wrong with itself, and if it is not,
          a reviewer can see exactly what it caught. */}
      <section aria-labelledby="excluded-heading">
        <h2 id="excluded-heading" className="text-lg font-semibold">
          Excluded from the score ({rejected.length})
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Quotations the system could not find in the source document. These were removed before
          anything was scored, and are shown so you can see what was caught.
        </p>
        {rejected.length > 0 && (
          <ul className="mt-3 space-y-3">
            {rejected.map((item) => (
              <EvidenceCard key={item.evidence_id} runId={runId} item={item} rejected />
            ))}
          </ul>
        )}
      </section>

      {/* Collapsed and below everything else. The position is the decision: an
          eligibility question read first frames a person as a problem before
          anything about their work has been read. */}
      {eligibility.length > 0 && (
        <Accordion type="single" collapsible className="rounded-lg border bg-card px-4">
          <AccordionItem value="eligibility">
            <AccordionTrigger>Eligibility</AccordionTrigger>
            <AccordionContent>
              <ul className="space-y-3">
                {eligibility.map((assessment) => (
                  <li key={assessment.criterion_id}>
                    <p className="text-sm">
                      <strong>{byId[assessment.criterion_id]?.label ?? assessment.criterion_id}</strong> —{' '}
                      {vocabulary?.states[assessment.resolved_state] ?? assessment.resolved_state}
                    </p>
                    {assessment.evidence
                      .filter((item) => item.verbatim_span)
                      .map((item) => (
                        <Quotation key={item.evidence_id} text={item.verbatim_span!} />
                      ))}
                  </li>
                ))}
              </ul>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      )}
    </>
  )
}

// The document's own words, set apart from interface copy. Rendered as text,
// never as markup: a CV containing asterisks is not formatting.
function Quotation({ text }: { text: string }) {
  return (
    <blockquote className="mt-2 border-l-2 border-primary/40 pl-3 font-[family-name:var(--font-quote)] text-[15px] leading-relaxed">
      {text}
    </blockquote>
  )
}

function EvidenceCard({ runId, item, rejected = false }: { runId: string; item: EvidenceItem; rejected?: boolean }) {
  const [passage, setPassage] = useState<Passage | null>(null)
  const [loading, setLoading] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const show = () => {
    setLoading(true)
    api
      .passage(runId, item.evidence_id)
      .then(setPassage)
      .catch((error: Error) => setFailure(error.message))
      .finally(() => setLoading(false))
  }

  return (
    <li className={`rounded-md border p-3 ${rejected ? 'border-dashed bg-muted/40' : 'bg-card'}`}>
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-medium">{item.claim}</span>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          <Badge variant={rejected ? 'destructive' : 'secondary'}>
            {rejected ? 'Not found in the document' : EVIDENCE_STATE[item.state]}
          </Badge>
          {item.provenance && <span>page {item.provenance.page_start}</span>}
          <span>confidence {Math.round(item.confidence * 100)}%</span>
        </span>
      </div>
      {item.verbatim_span && <Quotation text={item.verbatim_span} />}
      {item.verbatim_span && !rejected && (
        <div className="mt-2">
          {passage ? (
            <div className="rounded bg-muted p-3 text-sm leading-relaxed">
              <p className="mb-1 text-xs font-medium text-muted-foreground">Page {passage.page}</p>
              <p className="font-[family-name:var(--font-quote)]">
                …{passage.before}
                <mark className="rounded bg-primary/15 px-0.5 text-foreground">{passage.quoted}</mark>
                {passage.after}…
              </p>
            </div>
          ) : failure ? (
            <p className="text-xs text-muted-foreground">{failure}</p>
          ) : (
            <Button size="sm" variant="ghost" onClick={show} disabled={loading}>
              {loading ? 'Finding it…' : 'Show me where'}
            </Button>
          )}
        </div>
      )}
    </li>
  )
}
