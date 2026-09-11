import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { useVocabulary } from '@/lib/vocabulary'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { Textarea } from '@/components/ui/textarea'
import {
  ApiError,
  api,
  type Assessment,
  type DecisionBody,
  type Health,
  type Override,
  type Preview,
  type Recommendation,
  type Rubric,
  type RunRow,
} from '@/lib/api'
import { navigate } from '@/lib/router'

// Short enough to type, long enough to be a reason. A single word is not one.
const MIN_REASON_CHARS = 10

const ACTIONS: { action: DecisionBody['action']; label: string; confirm: string; primary?: boolean }[] = [
  {
    action: 'approve',
    label: 'Approve',
    confirm: 'Record your approval? The package becomes ready to send; sending is a separate step.',
    primary: true,
  },
  {
    action: 'request_info',
    label: 'Ask for more information',
    confirm: 'Send the information requests to the candidate and keep this one open?',
  },
  {
    action: 'reject',
    label: 'Do not proceed',
    confirm: 'Record that this candidate will not proceed? Nothing is sent to them.',
  },
]

type Draft = { new_state: string; reason_code: string; reason_text: string }

export function Decision({
  run,
  rubric,
  assessments,
  recommendation,
  health,
  openedAt,
}: {
  run: RunRow
  rubric: Rubric
  assessments: Assessment[]
  recommendation: Recommendation | null
  health: Health | null
  openedAt: number
}) {
  const vocabulary = useVocabulary()
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [preview, setPreview] = useState<Preview | null>(null)
  const [trust, setTrust] = useState(3)
  const [comments, setComments] = useState('')
  const [pending, setPending] = useState<(typeof ACTIONS)[number] | null>(null)
  const [busy, setBusy] = useState(false)

  const byId = Object.fromEntries(assessments.map((item) => [item.criterion_id, item]))

  // An override that changes nothing or carries no reason is not an override,
  // and is refused here rather than three layers down.
  const overrides = useMemo<Override[]>(
    () =>
      Object.entries(drafts).flatMap(([criterionId, draft]) => {
        const current = byId[criterionId]?.resolved_state
        if (!current || draft.new_state === current || draft.reason_text.trim().length < MIN_REASON_CHARS) {
          return []
        }
        return [
          {
            criterion_id: criterionId,
            previous_state: current,
            new_state: draft.new_state,
            reason_code: draft.reason_code,
            reason_text: draft.reason_text.trim(),
          },
        ]
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [drafts, assessments],
  )

  // The preview is computed by the same function that computes the stored
  // band, so the number shown is the number that gets saved.
  useEffect(() => {
    if (overrides.length === 0 || !recommendation) {
      setPreview(null)
      return
    }
    let cancelled = false
    api
      .preview(run.run_id, overrides)
      .then((result) => !cancelled && setPreview(result))
      .catch(() => !cancelled && setPreview(null))
    return () => {
      cancelled = true
    }
  }, [overrides, recommendation, run.run_id])

  const decide = async () => {
    if (!pending) return
    setBusy(true)
    try {
      const result = await api.decide(run.run_id, {
        action: pending.action,
        overrides,
        comments: comments.trim() || null,
        elapsed_seconds: Math.max(0, Math.round((Date.now() - openedAt) / 1000)),
        trust_rating: trust,
        expected_version: run.version,
      })
      toast.success(result.message)
      navigate({ page: 'queue' })
    } catch (failure) {
      const message = failure instanceof ApiError ? failure.message : 'The decision could not be recorded.'
      toast.error(message)
    } finally {
      setBusy(false)
      setPending(null)
    }
  }

  const decidable = run.chip.needs_attention
  const reviewerMissing = health !== null && !health.reviewer_configured

  return (
    <section aria-labelledby="decision-heading" className="space-y-5 border-t pt-6">
      <h2 id="decision-heading" className="text-lg font-semibold">
        Your decision
      </h2>

      {!decidable && (
        <p className="text-sm text-muted-foreground">
          This candidate has already been decided ({run.chip.label}).
        </p>
      )}

      {decidable && (
        <>
          <Accordion type="single" collapsible className="rounded-lg border bg-card px-4">
            <AccordionItem value="corrections">
              <AccordionTrigger>Correct an assessment</AccordionTrigger>
              <AccordionContent className="space-y-5">
                <p className="text-sm text-muted-foreground">
                  Change what a point resolved to. The recommendation is recalculated by the same
                  rules the system used; you never type a band.
                </p>
                {rubric.criteria
                  .filter((criterion) => byId[criterion.id])
                  .map((criterion) => {
                    const current = byId[criterion.id].resolved_state
                    const draft = drafts[criterion.id] ?? {
                      new_state: current,
                      reason_code: 'evidence_misread',
                      reason_text: '',
                    }
                    const changed = draft.new_state !== current
                    const update = (patch: Partial<Draft>) =>
                      setDrafts((all) => ({ ...all, [criterion.id]: { ...draft, ...patch } }))
                    return (
                      <div key={criterion.id} className="grid gap-2 sm:grid-cols-[1fr_1fr]">
                        <div className="sm:col-span-2 font-medium">{criterion.label}</div>
                        <div className="grid gap-1.5">
                          <Label htmlFor={`state-${criterion.id}`}>What it should be</Label>
                          <Select value={draft.new_state} onValueChange={(value) => update({ new_state: value })}>
                            <SelectTrigger id={`state-${criterion.id}`} className="w-full">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {Object.entries(vocabulary?.states ?? {}).map(([value, label]) => (
                                <SelectItem key={value} value={value}>
                                  {label}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        {changed && (
                          <>
                            <div className="grid gap-1.5">
                              <Label htmlFor={`reason-${criterion.id}`}>Why</Label>
                              <Select
                                value={draft.reason_code}
                                onValueChange={(value) => update({ reason_code: value })}
                              >
                                <SelectTrigger id={`reason-${criterion.id}`} className="w-full">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {Object.entries(vocabulary?.reasons ?? {}).map(([value, label]) => (
                                    <SelectItem key={value} value={value}>
                                      {label}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>
                            <div className="grid gap-1.5 sm:col-span-2">
                              <Label htmlFor={`text-${criterion.id}`}>What did the system get wrong?</Label>
                              <Textarea
                                id={`text-${criterion.id}`}
                                value={draft.reason_text}
                                onChange={(event) => update({ reason_text: event.target.value })}
                                placeholder="The CV describes this on page two, under Northwind Logistics."
                                aria-describedby={`hint-${criterion.id}`}
                              />
                              <p id={`hint-${criterion.id}`} className="text-xs text-muted-foreground">
                                {draft.reason_text.trim().length < MIN_REASON_CHARS
                                  ? 'A sentence is needed here before this change can be saved.'
                                  : 'This reason is recorded with your decision.'}
                              </p>
                            </div>
                          </>
                        )}
                      </div>
                    )
                  })}
              </AccordionContent>
            </AccordionItem>
          </Accordion>

          {preview && recommendation && (
            <PreviewPanel preview={preview} bands={vocabulary?.bands ?? {}} />
          )}

          <div className="grid gap-2">
            <Label htmlFor="trust">How much did you trust this assessment? ({trust} of 5)</Label>
            <Slider
              id="trust"
              min={1}
              max={5}
              step={1}
              value={[trust]}
              onValueChange={([value]) => setTrust(value)}
              aria-valuetext={`${trust} of 5`}
            />
            <p className="text-xs text-muted-foreground">
              One is not at all, five is completely. Recorded so the system can be measured.
            </p>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="comments">Anything to note?</Label>
            <Textarea
              id="comments"
              value={comments}
              onChange={(event) => setComments(event.target.value)}
              placeholder="Optional."
            />
          </div>

          {reviewerMissing && (
            <Alert>
              <AlertTitle>No reviewer is configured.</AlertTitle>
              <AlertDescription>
                Decisions have to be attributed to somebody. Set REVIEWER_ID where the API runs.
              </AlertDescription>
            </Alert>
          )}

          <div className="grid gap-2 sm:grid-cols-3">
            {ACTIONS.map((item) => (
              <Button
                key={item.action}
                variant={item.primary ? 'default' : 'outline'}
                onClick={() => setPending(item)}
                disabled={reviewerMissing}
                className="min-h-11"
              >
                {item.label}
              </Button>
            ))}
          </div>
        </>
      )}

      <Dialog open={pending !== null} onOpenChange={(open) => !open && !busy && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{pending?.label}</DialogTitle>
            <DialogDescription>{pending?.confirm}</DialogDescription>
          </DialogHeader>
          {overrides.length > 0 && (
            <p className="text-sm">
              {overrides.length} correction{overrides.length === 1 ? '' : 's'} will be recorded with
              this decision.
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPending(null)} disabled={busy}>
              Go back
            </Button>
            <Button onClick={decide} disabled={busy}>
              {busy ? 'Recording…' : 'Confirm'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}

function PreviewPanel({ preview, bands }: { preview: Preview; bands: Record<string, string> }) {
  const score = (item: Recommendation) =>
    item.score === null ? 'No score: not enough of the rubric was assessable.' : `Score ${item.score.toFixed(2)}`
  return (
    <div className="rounded-lg border bg-card p-4" aria-live="polite">
      <p className="font-medium">
        {preview.changed.length} assessment{preview.changed.length === 1 ? '' : 's'} changed.
      </p>
      <div className="mt-2 grid gap-4 sm:grid-cols-2">
        <div>
          <p className="text-xs text-muted-foreground">Now</p>
          <p className="font-medium">{bands[preview.before.band] ?? preview.before.band}</p>
          <p className="text-xs text-muted-foreground">{score(preview.before)}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">After your changes</p>
          <p className="font-medium">{bands[preview.after.band] ?? preview.after.band}</p>
          <p className="text-xs text-muted-foreground">{score(preview.after)}</p>
        </div>
      </div>
      {preview.after.band === preview.before.band && (
        <p className="mt-2 text-sm text-muted-foreground">The recommendation does not change.</p>
      )}
      {preview.after.requires_human && !preview.before.requires_human && (
        <p className="mt-2 text-sm text-warning">
          These changes mean this candidate now needs a person's judgement.
        </p>
      )}
    </div>
  )
}
