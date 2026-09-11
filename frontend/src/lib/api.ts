// The API, and the shapes it returns. Every type here is a contract's JSON as
// the backend emits it; nothing is computed on this side beyond display.

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init)
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: string }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // The body was not JSON; the status text is what we have.
    }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// --- shapes ------------------------------------------------------------------

export type Chip = { label: string; needs_attention: boolean; help: string }

export type Vocabulary = {
  chips: Record<string, Chip>
  bands: Record<string, string>
  states: Record<string, string>
  reasons: Record<string, string>
  detectors: Record<string, string>
  severities: Record<string, string>
  filters: Record<string, string[]>
  empty: Record<string, string>
}

export type RunRow = {
  run_id: string
  candidate_id: string
  role_id: string
  status: string
  started_at: string
  finished_at: string | null
  final_band: string | null
  integrity_tier: 'clean' | 'suspect' | 'quarantine'
  override_count: number
  version: number
  chip: Chip
  band_label: string
}

export type Provenance = {
  document_id: string
  page_start: number
  page_end: number
  norm_start: number
  norm_end: number
}

export type EvidenceItem = {
  evidence_id: string
  criterion_id: string
  state: 'supported' | 'contradicted' | 'insufficient_evidence'
  claim: string
  verbatim_span: string | null
  provenance: Provenance | null
  confidence: number
  span_validation: string
}

export type Assessment = {
  criterion_id: string
  evidence: EvidenceItem[]
  rejected_evidence: EvidenceItem[]
  resolved_state: string
  resolution_rule_id: string
  unassessed_reason: string | null
}

export type Criterion = {
  id: string
  label: string
  question: string
  weight: number
  kind: 'standard' | 'high_stakes' | 'blocker'
}

export type Rubric = {
  role_id: string
  role_title: string
  version: string
  criteria: Criterion[]
  notes_for_reviewer: string | null
}

export type DerivationStep = { rule_id: string; description: string }

export type Recommendation = {
  band: string
  score: number | null
  coverage: number
  criterion_states: Record<string, string>
  derivation: DerivationStep[]
  requires_human: boolean
  requires_human_reasons: string[]
}

export type Finding = { detector: string; severity: string; excerpt: string }
export type Report = { document_id: string; tier: string; findings: Finding[] }

export type Detail = {
  run: RunRow
  rubric: Rubric
  banner: { tier: string; message: string }
  recommendation: Recommendation | null
  assessments: Assessment[]
  documents: { document_id: string; original_filename: string; page_count: number | null }[]
  reports: Report[]
}

export type Passage = { page: number; before: string; quoted: string; after: string }

export type Override = {
  criterion_id: string
  previous_state: string
  new_state: string
  reason_code: string
  reason_text: string
}

export type Preview = { before: Recommendation; after: Recommendation; changed: string[] }

export type DecisionBody = {
  action: 'approve' | 'reject' | 'request_info'
  overrides: Override[]
  comments: string | null
  elapsed_seconds: number
  trust_rating: number
  expected_version: number
}

export type DecisionResult = { status: string; band: string | null; message: string }

export type Health = {
  ok: boolean
  demo_mode: boolean
  reviewer_configured: boolean
  provider: string
}

// --- calls -------------------------------------------------------------------

export const api = {
  health: () => request<Health>('/api/health'),
  vocabulary: () => request<Vocabulary>('/api/vocabulary'),
  runs: (filter: string) => request<RunRow[]>(`/api/runs?filter=${encodeURIComponent(filter)}`),
  run: (id: string) => request<Detail>(`/api/runs/${id}`),
  passage: (runId: string, evidenceId: string) =>
    request<Passage>(`/api/runs/${runId}/passages/${evidenceId}`),
  preview: (runId: string, overrides: Override[]) =>
    request<Preview>(`/api/runs/${runId}/preview`, json({ overrides })),
  decide: (runId: string, body: DecisionBody) =>
    request<DecisionResult>(`/api/runs/${runId}/decision`, json(body)),
  upload: (files: { file: File; candidateId: string }[], roleId: string) => {
    const form = new FormData()
    for (const { file, candidateId } of files) {
      form.append('files', file)
      form.append('candidate_ids', candidateId)
    }
    form.append('role_id', roleId)
    return request<{ accepted: number; candidate_ids: string[] }>('/api/uploads', {
      method: 'POST',
      body: form,
    })
  },
}
