import { useState } from 'react'
import { FlaskConical } from 'lucide-react'
import { toast } from 'sonner'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RolePicker } from '@/components/RolePicker'
import { ApiError, api, type Health } from '@/lib/api'
import { useRoles } from '@/lib/roles'
import { navigate } from '@/lib/router'

// Stated before choosing files, and read from the same limits intake applies.
const MAX_FILE_MB = 20
const MAX_DOCUMENTS_PER_CANDIDATE = 6
const ACCEPT = '.pdf,.docx,.txt,.md'

// Which candidate a file probably belongs to. Deliberately simple, and shown
// to the recruiter afterwards so a wrong guess is corrected, not hidden.
function guessCandidate(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, '').toLowerCase()
  const noise = new Set(['cv', 'resume', 'resumé', 'cover', 'letter', 'portfolio', 'final', 'v1', 'v2'])
  const parts = stem.split(/[_\-\s]+/).filter((part) => part && !noise.has(part))
  return parts.slice(0, 2).join('-') || stem || 'candidate'
}

type Row = { file: File; candidateId: string }

export function Upload({ health }: { health: Health | null }) {
  const [rows, setRows] = useState<Row[]>([])
  const [busy, setBusy] = useState(false)
  const roles = useRoles()
  const [chosenRole, setRoleId] = useState('')
  const roleId = chosenRole || roles?.[0]?.role_id || ''

  if (health?.demo_mode) {
    return (
      <Alert>
        <FlaskConical aria-hidden="true" />
        <AlertTitle>Demo mode is on, so uploads are switched off.</AlertTitle>
        <AlertDescription>
          Only the synthetic candidates are read. Unset DEMO_MODE to process real documents.
        </AlertDescription>
      </Alert>
    )
  }

  const oversized = rows.filter((row) => row.file.size > MAX_FILE_MB * 1024 * 1024)
  const candidates = new Set(rows.map((row) => row.candidateId.trim() || 'candidate'))
  const tooMany = [...candidates].filter(
    (id) => rows.filter((row) => (row.candidateId.trim() || 'candidate') === id).length > MAX_DOCUMENTS_PER_CANDIDATE,
  )

  const choose = (files: FileList | null) => {
    if (!files) return
    setRows([...files].map((file) => ({ file, candidateId: guessCandidate(file.name) })))
  }

  const submit = async () => {
    setBusy(true)
    try {
      const result = await api.upload(
        rows.map((row) => ({ file: row.file, candidateId: row.candidateId.trim() || 'candidate' })),
        roleId,
      )
      toast.success(
        `${result.accepted} candidate${result.accepted === 1 ? '' : 's'} accepted. The queue shows progress.`,
      )
      navigate({ page: 'queue' })
    } catch (failure) {
      toast.error(failure instanceof ApiError ? failure.message : 'The files could not be sent.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="upload-heading" className="space-y-5">
      <div>
        <h1 id="upload-heading" className="text-2xl font-semibold tracking-tight">
          Add candidates
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          PDF, Word, or plain text. Up to {MAX_FILE_MB} MB per file and {MAX_DOCUMENTS_PER_CANDIDATE}{' '}
          documents per candidate. Files are stored by their contents, never by their filename.
        </p>
      </div>

      <div className="grid gap-4 rounded-xl border bg-card p-4 sm:grid-cols-2">
        <RolePicker id="upload-role" roles={roles} value={roleId} onChange={setRoleId} label="Assess against" />
        <div className="grid gap-1.5">
          <Label htmlFor="files">Choose files</Label>
          <Input
            id="files"
            type="file"
            multiple
            accept={ACCEPT}
            className="h-auto cursor-pointer py-2 file:mr-3 file:rounded-md file:bg-secondary file:px-2 file:py-1"
            onChange={(event) => choose(event.target.files)}
          />
        </div>
      </div>

      {rows.length > 0 && (
        <>
          <div>
            <h2 className="font-medium">Check the grouping</h2>
            <p className="text-sm text-muted-foreground">
              One row per file. Change a candidate name if two files belong to the same person, or
              if a name was guessed wrongly.
            </p>
          </div>
          <ul className="divide-y rounded-lg border bg-card">
            {rows.map((row, index) => (
              <li key={`${row.file.name}-${index}`} className="grid gap-2 p-3 sm:grid-cols-[1fr_2fr] sm:items-center">
                <div className="grid gap-1">
                  <Label htmlFor={`candidate-${index}`} className="sr-only">
                    Candidate for {row.file.name}
                  </Label>
                  <Input
                    id={`candidate-${index}`}
                    value={row.candidateId}
                    onChange={(event) =>
                      setRows((all) => all.map((item, i) => (i === index ? { ...item, candidateId: event.target.value } : item)))
                    }
                  />
                </div>
                <div className="text-sm text-muted-foreground">
                  {row.file.name} · {Math.round(row.file.size / 1024)} KB
                </div>
              </li>
            ))}
          </ul>
          <p className="text-sm">
            <strong>
              {candidates.size} candidate{candidates.size === 1 ? '' : 's'}, {rows.length} document
              {rows.length === 1 ? '' : 's'}.
            </strong>
          </p>
          {oversized.length > 0 && (
            <p role="alert" className="text-sm text-destructive">
              These files are larger than this system accepts: {oversized.map((row) => row.file.name).join(', ')}
            </p>
          )}
          {tooMany.length > 0 && (
            <p role="alert" className="text-sm text-destructive">
              More than {MAX_DOCUMENTS_PER_CANDIDATE} documents for: {tooMany.join(', ')}
            </p>
          )}
          <Button onClick={submit} disabled={busy || !roleId || oversized.length > 0 || tooMany.length > 0} className="min-h-11">
            {busy
              ? 'Sending…'
              : `Process ${candidates.size} candidate${candidates.size === 1 ? '' : 's'} as ${
                  roles?.find((role) => role.role_id === roleId)?.role_title ?? 'this role'
                }`}
          </Button>
        </>
      )}
    </section>
  )
}
