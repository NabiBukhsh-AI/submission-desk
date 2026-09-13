import { useEffect, useState } from 'react'
import { BookOpenCheck, ChevronDown, Copy, FileCode2, Plus, RotateCcw, Save, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { ApiError, api, type CriterionData, type Role, type RubricData, type RubricView } from '@/lib/api'
import { href, navigate } from '@/lib/router'
import { cn } from '@/lib/utils'

// What a role asks for, editable. The whole rubric is sent to the API and
// validated there as one thing; nothing is stored until all of it holds.
// ponytail: state_points are kept as they are (the YAML view shows them); a
// points editor if anyone ever needs a criterion worth 0.7 for "partial".

const KINDS: { value: CriterionData['kind']; label: string; help: string }[] = [
  { value: 'standard', label: 'Standard', help: 'Counts toward the score by its weight.' },
  { value: 'high_stakes', label: 'High stakes', help: 'Needs two quotations, and escalates to the strong model tier.' },
  { value: 'blocker', label: 'Blocker', help: 'Decides the outcome on its own; unaddressed, it routes to a person.' },
]

const DEFAULT_POINTS = { met: 1.0, partial: 0.5, not_met: 0.0, contradicted: 0.0 }

const lines = (items?: string[]) => (items ?? []).join('\n')
const fromLines = (text: string) => text.split('\n').map((line) => line.trim()).filter(Boolean)
const slug = (text: string) =>
  text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64)

export function Roles({ roleId }: { roleId?: string }) {
  const [roles, setRoles] = useState<Role[] | null>(null)
  const [view, setView] = useState<RubricView | null>(null)
  const [data, setData] = useState<RubricData | null>(null)
  const [busy, setBusy] = useState(false)
  const [showYaml, setShowYaml] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadRoles = () => api.roles().then(setRoles).catch((failure: Error) => setError(failure.message))

  useEffect(() => {
    loadRoles()
  }, [])

  // Keyed by role in App, so a different role is a fresh component.
  useEffect(() => {
    if (!roleId) return
    api
      .rubric(roleId)
      .then((loaded) => {
        setView(loaded)
        setData(loaded.data)
      })
      .catch((failure: Error) => setError(failure.message))
  }, [roleId])

  const dirty = view !== null && data !== null && JSON.stringify(data) !== JSON.stringify(view.data)

  const save = async (targetId = roleId) => {
    if (!data || !targetId) return
    setBusy(true)
    try {
      const saved = await api.saveRubric(targetId, data)
      setView(saved)
      setData(saved.data)
      await loadRoles()
      toast.success(`${saved.data.role_title} saved. In force on the next run.`)
      if (targetId !== roleId) navigate({ page: 'roles', roleId: targetId })
    } catch (failure) {
      toast.error(failure instanceof ApiError ? failure.message : 'The rubric could not be saved.', {
        duration: 8000,
      })
    } finally {
      setBusy(false)
    }
  }

  const reset = async () => {
    if (!roleId || !view) return
    const fromFile = roles?.some((role) => role.role_id === roleId && role.source === 'admin')
    if (!fromFile) return
    if (!window.confirm('Discard the saved changes and go back to the shipped file? A role that exists only here is removed.')) return
    setBusy(true)
    try {
      await api.resetRubric(roleId)
      const still = (await api.roles()).find((role) => role.role_id === roleId)
      setRoles(await api.roles())
      if (still) {
        const loaded = await api.rubric(roleId)
        setView(loaded)
        setData(loaded.data)
      } else {
        navigate({ page: 'roles' })
      }
      toast.success('Back to the shipped rubric.')
    } catch (failure) {
      toast.error(failure instanceof ApiError ? failure.message : 'The rubric could not be reset.')
    } finally {
      setBusy(false)
    }
  }

  const duplicate = () => {
    if (!data) return
    const title = window.prompt('Title for the new role', `${data.role_title} (copy)`)
    if (!title) return
    const id = slug(window.prompt('Identifier for the new role (lowercase, dashes)', slug(title)) ?? '')
    if (!id) return
    setData({ ...data, role_id: id, role_title: title })
    void save(id)
  }

  const update = (patch: Partial<RubricData>) => setData((current) => (current ? { ...current, ...patch } : current))
  const updateCriterion = (index: number, patch: Partial<CriterionData>) =>
    setData((current) =>
      current
        ? { ...current, criteria: current.criteria.map((item, at) => (at === index ? { ...item, ...patch } : item)) }
        : current,
    )
  const addCriterion = () =>
    setData((current) =>
      current
        ? {
            ...current,
            criteria: [
              ...current.criteria,
              {
                id: `new-requirement-${current.criteria.length + 1}`,
                label: '',
                question: '',
                kind: 'standard',
                weight: 3,
                min_supported: 1,
                state_points: { ...DEFAULT_POINTS },
                positive_examples: [],
                negative_examples: [],
              },
            ],
          }
        : current,
    )
  const removeCriterion = (index: number) =>
    setData((current) =>
      current ? { ...current, criteria: current.criteria.filter((_, at) => at !== index) } : current,
    )

  if (error) {
    return (
      <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-4">
        {error}
      </p>
    )
  }

  return (
    <section aria-labelledby="roles-heading" className="space-y-6">
      <div>
        <h1 id="roles-heading" className="text-2xl font-semibold">
          Roles
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          A role is a rubric: what is asked, what each answer is worth, and where the bands sit. Saved changes are
          in force on the next run; every run records the hash of the rubric it used.
        </p>
      </div>

      <div className="grid min-w-0 gap-6 lg:grid-cols-[16rem_1fr]">
        <nav
          aria-label="Roles"
          className="-mx-4 flex min-w-0 gap-2 overflow-x-auto px-4 pb-1 lg:sticky lg:top-20 lg:mx-0 lg:block lg:space-y-1 lg:self-start lg:px-0"
        >
          {roles === null ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            roles.map((role) => (
              <a
                key={role.role_id}
                href={href({ page: 'roles', roleId: role.role_id })}
                aria-current={role.role_id === roleId ? 'page' : undefined}
                className={cn(
                  'block shrink-0 rounded-lg border px-3 py-2.5 transition-colors',
                  role.role_id === roleId ? 'border-primary/50 bg-primary/5' : 'bg-card hover:border-primary/30',
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{role.role_title}</span>
                  {role.source === 'admin' && <Badge variant="secondary">edited</Badge>}
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {role.criteria} requirements · v{role.version}
                </div>
              </a>
            ))
          )}
        </nav>

        {!data || !view ? (
          <div className="flex min-h-64 items-center justify-center rounded-xl border border-dashed text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-2">
              <BookOpenCheck className="size-4" aria-hidden="true" />
              Choose a role to see what it asks for.
            </span>
          </div>
        ) : (
          <div className="min-w-0 space-y-5">
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <CardTitle className="text-xl">{view.data.role_title}</CardTitle>
                    <CardDescription className="mt-1">
                      <code className="text-xs">{view.role_id}</code> · rubric hash{' '}
                      <code className="text-xs">{view.rubric_hash.slice(0, 12)}</code> ·{' '}
                      {view.source === 'admin' ? 'edited on this page' : 'as shipped in rubrics/'}
                    </CardDescription>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" variant="outline" size="sm" onClick={duplicate} disabled={busy}>
                      <Copy aria-hidden="true" />
                      Duplicate as new role
                    </Button>
                    {view.source === 'admin' && (
                      <Button type="button" variant="outline" size="sm" onClick={reset} disabled={busy}>
                        <RotateCcw aria-hidden="true" />
                        Reset to file
                      </Button>
                    )}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="grid gap-4 sm:grid-cols-3">
                <div className="space-y-1.5 sm:col-span-2">
                  <Label htmlFor="role-title">Role title</Label>
                  <Input id="role-title" value={data.role_title} onChange={(event) => update({ role_title: event.target.value })} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="role-version">Version</Label>
                  <Input id="role-version" value={data.version} onChange={(event) => update({ version: event.target.value })} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="min-coverage">Coverage gate</Label>
                  <Input
                    id="min-coverage"
                    inputMode="decimal"
                    value={String(data.min_coverage ?? 0.7)}
                    onChange={(event) => update({ min_coverage: Number(event.target.value) })}
                  />
                  <p className="text-xs text-muted-foreground">
                    Fraction of the rubric, by weight, that must be assessable before a score is reported.
                  </p>
                </div>
                <div className="space-y-1.5 sm:col-span-2">
                  <Label htmlFor="notes">Notes shown to the reviewer</Label>
                  <Textarea
                    id="notes"
                    value={data.notes_for_reviewer ?? ''}
                    onChange={(event) => update({ notes_for_reviewer: event.target.value || null })}
                  />
                </div>
              </CardContent>
            </Card>

            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">Requirements</h2>
              <Button type="button" variant="outline" size="sm" onClick={addCriterion}>
                <Plus aria-hidden="true" />
                Add requirement
              </Button>
            </div>

            <ol className="space-y-4">
              {data.criteria.map((criterion, index) => (
                <li key={index}>
                  <details className="group rounded-xl border bg-card" open={!criterion.label}>
                    <summary className="flex min-h-14 cursor-pointer list-none items-center gap-3 px-4 py-3 [&::-webkit-details-marker]:hidden">
                      <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
                      <span className="font-heading text-xs text-muted-foreground">{index + 1}</span>
                      <span className="min-w-0 flex-1 truncate font-medium">{criterion.label || 'New requirement'}</span>
                      <Badge variant="outline" className="hidden shrink-0 sm:inline-flex">
                        {KINDS.find((kind) => kind.value === criterion.kind)?.label}
                      </Badge>
                      <span className="shrink-0 text-xs text-muted-foreground">weight {criterion.weight}</span>
                    </summary>
                    <div className="grid gap-4 border-t px-4 py-4 sm:grid-cols-6">
                      <div className="flex items-center justify-end sm:col-span-6">
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => removeCriterion(index)}
                          aria-label={`Remove requirement ${index + 1}`}
                        >
                          <Trash2 aria-hidden="true" />
                          Remove
                        </Button>
                      </div>
                      <div className="space-y-1.5 sm:col-span-4">
                        <Label htmlFor={`label-${index}`}>Requirement</Label>
                        <Input
                          id={`label-${index}`}
                          value={criterion.label}
                          onChange={(event) => updateCriterion(index, { label: event.target.value })}
                        />
                      </div>
                      <div className="space-y-1.5 sm:col-span-2">
                        <Label htmlFor={`id-${index}`}>Identifier</Label>
                        <Input
                          id={`id-${index}`}
                          value={criterion.id}
                          onChange={(event) => updateCriterion(index, { id: slug(event.target.value) })}
                        />
                      </div>
                      <div className="space-y-1.5 sm:col-span-6">
                        <Label htmlFor={`question-${index}`}>The question the model answers with quotations</Label>
                        <Textarea
                          id={`question-${index}`}
                          value={criterion.question}
                          onChange={(event) => updateCriterion(index, { question: event.target.value })}
                        />
                      </div>
                      <div className="space-y-1.5 sm:col-span-2">
                        <Label htmlFor={`kind-${index}`}>Kind</Label>
                        <Select
                          value={criterion.kind}
                          onValueChange={(value) =>
                            updateCriterion(index, {
                              kind: value as CriterionData['kind'],
                              min_supported: value === 'high_stakes' ? Math.max(criterion.min_supported ?? 1, 2) : Math.max(criterion.min_supported ?? 1, 1),
                            })
                          }
                        >
                          <SelectTrigger id={`kind-${index}`} className="w-full">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {KINDS.map((kind) => (
                              <SelectItem key={kind.value} value={kind.value}>
                                {kind.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">{KINDS.find((kind) => kind.value === criterion.kind)?.help}</p>
                      </div>
                      <div className="space-y-1.5 sm:col-span-2">
                        <Label htmlFor={`weight-${index}`}>Weight (1–10)</Label>
                        <Input
                          id={`weight-${index}`}
                          type="number"
                          min={1}
                          max={10}
                          value={criterion.weight}
                          onChange={(event) => updateCriterion(index, { weight: Number(event.target.value) })}
                        />
                      </div>
                      <div className="space-y-1.5 sm:col-span-2">
                        <Label htmlFor={`min-${index}`}>Quotations needed</Label>
                        <Input
                          id={`min-${index}`}
                          type="number"
                          min={criterion.kind === 'blocker' ? 1 : 0}
                          value={criterion.min_supported ?? 1}
                          onChange={(event) => updateCriterion(index, { min_supported: Number(event.target.value) })}
                        />
                      </div>
                      <div className="space-y-1.5 sm:col-span-3">
                        <Label htmlFor={`pos-${index}`}>What counts (one per line)</Label>
                        <Textarea
                          id={`pos-${index}`}
                          value={lines(criterion.positive_examples)}
                          onChange={(event) => updateCriterion(index, { positive_examples: fromLines(event.target.value) })}
                        />
                      </div>
                      <div className="space-y-1.5 sm:col-span-3">
                        <Label htmlFor={`neg-${index}`}>What does not (one per line)</Label>
                        <Textarea
                          id={`neg-${index}`}
                          value={lines(criterion.negative_examples)}
                          onChange={(event) => updateCriterion(index, { negative_examples: fromLines(event.target.value) })}
                        />
                      </div>
                    </div>
                  </details>
                </li>
              ))}
            </ol>

            <Card>
              <CardHeader>
                <CardTitle>Bands</CardTitle>
                <CardDescription>
                  The score a candidate needs for each outcome. Listed from the highest cutoff down; the lowest must be 0.
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-3 sm:grid-cols-4">
                {data.bands.map((band, index) => (
                  <div key={band.band} className="space-y-1.5">
                    <Label htmlFor={`band-${index}`}>{band.band.replaceAll('_', ' ')}</Label>
                    <Input
                      id={`band-${index}`}
                      inputMode="decimal"
                      value={String(band.min_score)}
                      onChange={(event) =>
                        update({
                          bands: data.bands.map((item, at) =>
                            at === index ? { ...item, min_score: Number(event.target.value) } : item,
                          ),
                        })
                      }
                    />
                  </div>
                ))}
              </CardContent>
            </Card>

            <div className="sticky bottom-20 flex flex-wrap items-center gap-3 rounded-xl border bg-card/95 p-3 shadow-sm backdrop-blur md:bottom-4">
              <Button type="button" onClick={() => save()} disabled={busy || !dirty}>
                <Save aria-hidden="true" />
                Save role
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={() => setShowYaml((open) => !open)}>
                <FileCode2 aria-hidden="true" />
                {showYaml ? 'Hide YAML' : 'Show as YAML'}
              </Button>
              <span className="text-xs text-muted-foreground">
                {dirty ? 'Unsaved changes. The whole rubric is checked before anything is stored.' : 'No changes.'}
              </span>
            </div>

            {showYaml && (
              <pre className="overflow-x-auto rounded-xl border bg-card p-4 text-xs leading-relaxed">
                <code>{view.yaml}</code>
              </pre>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
