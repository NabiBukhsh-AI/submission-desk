import { useEffect, useState } from 'react'
import { FlaskConical, KeyRound, PlugZap, Save, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ApiError, api, type Probe, type Provider, type SettingsView } from '@/lib/api'

// Prices are USD per million tokens as the providers publish them. A preset
// fills the model id and both prices for a tier; the fields stay editable
// because prices change and this page is where the change is recorded.
type Preset = { id: string; label: string; input: string; output: string }

const PRESETS: Preset[] = [
  { id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5', input: '1', output: '5' },
  { id: 'claude-sonnet-5', label: 'Claude Sonnet 5', input: '2', output: '10' },
  { id: 'claude-opus-5', label: 'Claude Opus 5', input: '5', output: '25' },
]

const PROVIDER_LABELS: Record<Provider, string> = {
  fake: 'Offline stand-in (no API, no cost)',
  anthropic: 'Anthropic (Claude)',
}

const KEY_HELP = 'From console.anthropic.com → API keys. Starts with sk-ant-.'

const EDITABLE = [
  'model_provider',
  'model_cheap_id',
  'model_strong_id',
  'model_api_base_url',
  'price_cheap_input',
  'price_cheap_output',
  'price_strong_input',
  'price_strong_output',
  'reviewer_id',
  'blind_mode',
  'retention_days',
] as const

export function Admin() {
  const [view, setView] = useState<SettingsView | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [probe, setProbe] = useState<Probe[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () =>
    api.admin
      .settings()
      .then((loaded) => {
        setView(loaded)
        setValues(loaded.values)
      })
      .catch((failure) => setError(failure instanceof ApiError ? failure.message : 'The API is not reachable.'))

  useEffect(() => {
    load()
  }, [])

  if (error) {
    return (
      <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-4">
        {error}
      </p>
    )
  }
  if (!view) return <p className="text-sm text-muted-foreground">Loading settings…</p>

  const provider = (values.model_provider || 'fake') as Provider
  const set = (name: string, value: string) => setValues((current) => ({ ...current, [name]: value }))
  const applyPreset = (tier: 'cheap' | 'strong', preset: Preset) =>
    setValues((current) => ({
      ...current,
      [`model_${tier}_id`]: preset.id,
      [`price_${tier}_input`]: preset.input,
      [`price_${tier}_output`]: preset.output,
    }))

  const save = async () => {
    setBusy(true)
    setProbe(null)
    try {
      const changes = Object.fromEntries(EDITABLE.map((name) => [name, values[name] ?? '']))
      const saved = await api.admin.save(changes, provider === 'fake' ? {} : { [provider]: key })
      setView(saved)
      setValues(saved.values)
      setKey('')
      toast.success('Settings saved and in force.')
    } catch (failure) {
      toast.error(failure instanceof ApiError ? failure.message : 'The settings could not be saved.')
    } finally {
      setBusy(false)
    }
  }

  const clearKey = async () => {
    setBusy(true)
    try {
      await api.admin.clearKey(provider)
      await load()
      toast.success(`The ${PROVIDER_LABELS[provider]} key was removed.`)
    } catch (failure) {
      toast.error(failure instanceof ApiError ? failure.message : 'The key could not be removed.')
    } finally {
      setBusy(false)
    }
  }

  const test = async () => {
    setBusy(true)
    setProbe(null)
    try {
      setProbe(await api.admin.probe())
    } catch (failure) {
      toast.error(failure instanceof ApiError ? failure.message : 'The connection test could not run.')
    } finally {
      setBusy(false)
    }
  }

  const keySet = view.keys_set[provider] ?? false

  return (
    <section aria-labelledby="admin-heading" className="space-y-5">
      <div>
        <h1 id="admin-heading" className="text-2xl font-semibold tracking-tight">
          Settings
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Saved settings take effect on the next request. API keys are stored sealed and are never shown again.
        </p>
      </div>

      {view.demo_mode && (
        <Alert>
          <FlaskConical aria-hidden="true" />
          <AlertTitle>Demo mode is on.</AlertTitle>
          <AlertDescription>
            The API was started with DEMO_MODE set, so the offline stand-in answers every call whatever is chosen
            here. Start the API without <code>--demo</code> to use a provider on real documents.
          </AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Provider</CardTitle>
          <CardDescription>Who answers the model calls. Currently in force: {PROVIDER_LABELS[view.effective_provider] ?? view.effective_provider}.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="provider">Provider</Label>
            <Select value={provider} onValueChange={(value) => set('model_provider', value)}>
              <SelectTrigger id="provider" className="w-full max-w-md">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(PROVIDER_LABELS) as Provider[]).map((name) => (
                  <SelectItem key={name} value={name}>
                    {PROVIDER_LABELS[name]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {provider !== 'fake' && (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="api-key" className="flex items-center gap-2">
                  API key
                  <span className={keySet ? 'text-xs text-success' : 'text-xs text-muted-foreground'}>
                    {keySet ? '· a key is set' : '· no key set'}
                  </span>
                </Label>
                <div className="flex gap-2">
                  <Input
                    id="api-key"
                    type="password"
                    autoComplete="off"
                    placeholder={keySet ? 'Leave blank to keep the current key' : 'Paste the key'}
                    value={key}
                    onChange={(event) => setKey(event.target.value)}
                    className="max-w-md"
                  />
                  {keySet && (
                    <Button type="button" variant="outline" onClick={clearKey} disabled={busy}>
                      <Trash2 aria-hidden="true" />
                      Remove
                    </Button>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">{KEY_HELP}</p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="base-url">Base URL (optional)</Label>
                <Input
                  id="base-url"
                  placeholder="Only for a proxy or a compatible endpoint"
                  value={values.model_api_base_url ?? ''}
                  onChange={(event) => set('model_api_base_url', event.target.value)}
                  className="max-w-md"
                />
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {provider !== 'fake' && (
        <Card>
          <CardHeader>
            <CardTitle>Models and prices</CardTitle>
            <CardDescription>
              The cheap tier reads every document; the strong tier handles criteria the rubric marks high-stakes
              and the escalations. Haiku 4.5 on both is the cheap, good setup. Prices are USD per million tokens.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-6 md:grid-cols-2">
            {(['cheap', 'strong'] as const).map((tier) => (
              <fieldset key={tier} className="space-y-3">
                <legend className="text-sm font-medium capitalize">{tier} tier</legend>
                <div className="flex flex-wrap gap-1.5">
                  {PRESETS.map((preset) => (
                    <Button
                      key={preset.id}
                      type="button"
                      size="sm"
                      variant={values[`model_${tier}_id`] === preset.id ? 'secondary' : 'outline'}
                      onClick={() => applyPreset(tier, preset)}
                    >
                      {preset.label}
                    </Button>
                  ))}
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`${tier}-id`}>Model id</Label>
                  <Input
                    id={`${tier}-id`}
                    value={values[`model_${tier}_id`] ?? ''}
                    onChange={(event) => set(`model_${tier}_id`, event.target.value)}
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1.5">
                    <Label htmlFor={`${tier}-in`}>Input $/M</Label>
                    <Input
                      id={`${tier}-in`}
                      inputMode="decimal"
                      value={values[`price_${tier}_input`] ?? ''}
                      onChange={(event) => set(`price_${tier}_input`, event.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={`${tier}-out`}>Output $/M</Label>
                    <Input
                      id={`${tier}-out`}
                      inputMode="decimal"
                      value={values[`price_${tier}_output`] ?? ''}
                      onChange={(event) => set(`price_${tier}_output`, event.target.value)}
                    />
                  </div>
                </div>
              </fieldset>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Review</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="reviewer">Reviewer id</Label>
            <Input
              id="reviewer"
              placeholder="Who decisions are attributed to"
              value={values.reviewer_id ?? ''}
              onChange={(event) => set('reviewer_id', event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="retention">Retention (days)</Label>
            <Input
              id="retention"
              inputMode="numeric"
              value={values.retention_days ?? ''}
              onChange={(event) => set('retention_days', event.target.value)}
            />
          </div>
          <label className="flex min-h-11 items-center gap-2 text-sm md:mt-6">
            <input
              type="checkbox"
              className="size-4 accent-primary"
              checked={values.blind_mode === 'true'}
              onChange={(event) => set('blind_mode', event.target.checked ? 'true' : 'false')}
            />
            Blind mode: hide the recommendation until the reviewer commits
          </label>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" onClick={save} disabled={busy}>
          <Save aria-hidden="true" />
          Save settings
        </Button>
        <Button type="button" variant="outline" onClick={test} disabled={busy}>
          <PlugZap aria-hidden="true" />
          Test the connection
        </Button>
        <span className="text-xs text-muted-foreground">
          <KeyRound className="mr-1 inline size-3.5" aria-hidden="true" />
          The test makes one tiny call per tier through the saved provider and reports what each cost.
        </span>
      </div>

      {probe?.map((result) => (
        <Alert key={result.tier} variant={result.ok ? 'default' : 'destructive'} role="status">
          <PlugZap aria-hidden="true" />
          <AlertTitle>
            {result.tier.replace('tier_', '')} tier: {result.ok ? 'the provider answered.' : 'not working.'}
          </AlertTitle>
          <AlertDescription>
            {result.message}
            {result.ok && (
              <>
                {' '}
                ({result.input_tokens} in / {result.output_tokens} out tokens)
              </>
            )}
          </AlertDescription>
        </Alert>
      ))}
    </section>
  )
}
