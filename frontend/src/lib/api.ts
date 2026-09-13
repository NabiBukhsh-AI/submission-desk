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
  // The session is a cookie; a static build served from another origin
  // still has to send it.
  const response = await fetch(`${BASE}${path}`, { credentials: 'include', ...init })
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
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const json = (body: unknown, method = 'POST'): RequestInit => ({
  method,
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

export type AuthStatus = { setup_required: boolean; user: string | null }

export type Provider = 'fake' | 'anthropic'

export type SettingsView = {
  values: Record<string, string>
  keys_set: Record<string, boolean>
  demo_mode: boolean
  effective_provider: Provider
}

export type Probe = {
  ok: boolean
  message: string
  input_tokens: number
  output_tokens: number
  tier: string
}

export type Role = {
  role_id: string
  role_title: string
  version: string
  criteria: number
  source: 'file' | 'admin'
}

// The rubric as data, the shape of rubrics/<role>.yaml. Edited on the roles
// page and validated whole by the API before anything is stored.
export type CriterionData = {
  id: string
  label: string
  question: string
  kind: 'standard' | 'high_stakes' | 'blocker'
  weight: number
  min_supported?: number
  state_points: Record<string, number | null>
  positive_examples?: string[]
  negative_examples?: string[]
}

export type RubricData = {
  role_id: string
  role_title: string
  version: string
  min_coverage?: number
  blind_mode_default?: boolean
  forbidden_attributes?: string[]
  notes_for_reviewer?: string | null
  criteria: CriterionData[]
  bands: { band: string; min_score: number }[]
}

export type RubricView = {
  role_id: string
  source: 'file' | 'admin'
  rubric_hash: string
  data: RubricData
  yaml: string
}

export type Health = {
  ok: boolean
  demo_mode: boolean
  reviewer_configured: boolean
  provider: string
}

// --- calls -------------------------------------------------------------------

export const api = {
  health: () => request<Health>('/api/health'),
  auth: {
    status: () => request<AuthStatus>('/api/auth/status'),
    setup: (username: string, password: string) =>
      request<{ user: string }>('/api/auth/setup', json({ username, password })),
    login: (username: string, password: string) =>
      request<{ user: string }>('/api/auth/login', json({ username, password })),
    logout: () => request<void>('/api/auth/logout', { method: 'POST' }),
  },
  roles: () => request<Role[]>('/api/roles'),
  rubric: (roleId: string) => request<RubricView>(`/api/admin/rubrics/${roleId}`),
  saveRubric: (roleId: string, data: RubricData) =>
    request<RubricView>(`/api/admin/rubrics/${roleId}`, json({ data }, 'PUT')),
  resetRubric: (roleId: string) =>
    request<void>(`/api/admin/rubrics/${roleId}`, { method: 'DELETE' }),
  reassess: (runIds: string[], roleId: string) =>
    request<{ accepted: number; role_id: string }>(
      '/api/runs/reassess',
      json({ run_ids: runIds, role_id: roleId }),
    ),
  admin: {
    settings: () => request<SettingsView>('/api/admin/settings'),
    save: (changes: Record<string, string>, keys: Record<string, string>) =>
      request<SettingsView>('/api/admin/settings', json({ changes, keys }, 'PUT')),
    clearKey: (provider: string) =>
      request<void>(`/api/admin/keys/${provider}`, { method: 'DELETE' }),
    probe: () => request<Probe[]>('/api/admin/probe', { method: 'POST' }),
  },
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
