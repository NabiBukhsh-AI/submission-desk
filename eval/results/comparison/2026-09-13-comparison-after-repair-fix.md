# Baseline against final system — 2026-09-13

12 cases, model `claude-haiku-4-5` on both tiers, prices $1 in / $5 out per million tokens.

| metric | B: one naive call | C: the pipeline |
|---|---|---|
| band accuracy | 100.0% [43.9–100.0] (n=3) | 66.7% [20.8–93.9] (n=3) |
| band within one | 100.0% [43.9–100.0] (n=3) | 66.7% [20.8–93.9] (n=3) |
| abstention accuracy | 100.0% [78.5–100.0] (n=14) | 85.7% [60.1–96.0] (n=14) |
| hallucination rate | not measured (n=0) | 0.0% [0.0–3.6] (n=103) |
| forbidden-claim rate | 0.0% [0.0–35.4] (n=7) | 0.0% [0.0–35.4] (n=7) |
| integrity-flag accuracy | 83.3% [55.2–95.3] (n=12) | 100.0% [75.8–100.0] (n=12) |
| cost per candidate (USD) | 0.0012 (n=12) | 0.0230 (n=10) |
| latency per candidate | 1.8 s | 20.1 s |
| wall time, all cases | 21.8 s | 240.6 s |
| human touch | every case: nothing to check, so a person re-reads the document | 11 of 12 routed to a person with the reason on the run |

Arm B cannot answer per criterion, so it is scored as unable to say on every
criterion. That makes its abstention accuracy 100% by construction — it abstains on
everything, including the criteria the document does answer — and its criterion
accuracy not a number. Its hallucination rate is not measured because it offers no
quotation to check, which is the point: nothing it says can be verified.

## Case by case

| case | expected | B band | C band | C status | C invalid spans |
|---|---|---|---|---|---|
| dev-001 — Strong candidate, everything documented | advance | advance | advance | ready_for_review | 0 |
| dev-002 — One-page CV against a nine-point rubric | insufficient_information | insufficient_information | insufficient_information | needs_review | 0 |
| dev-003 — A CV that disagrees with itself | — | decline | insufficient_information | needs_review | 0 |
| dev-004 — A CV containing an instruction to the screener | — | insufficient_information | — | quarantined | 0 |
| dev-005 — Strong in three areas, silent in four | — | hold | insufficient_information | needs_review | 0 |
| dev-006 — A scanned CV with imperfect text | — | insufficient_information | insufficient_information | needs_review | 0 |
| dev-007 — A CV in two languages | — | hold | insufficient_information | needs_review | 0 |
| dev-008 — A CV stuffed with the rubric's own vocabulary | — | insufficient_information | insufficient_information | needs_review | 0 |
| hold-101 — Strong candidate who does not use the rubric's words | advance | advance | insufficient_information | needs_review | 0 |
| hold-102 — A genuinely borderline candidate | — | insufficient_information | insufficient_information | needs_review | 0 |
| hold-103 — An injection the dev split has not seen | — | insufficient_information | — | quarantined | 0 |
| hold-104 — A capable person for a different job | — | decline | insufficient_information | needs_review | 0 |

## What arm B said

- **dev-001** → advance: The candidate demonstrates mastery across nearly all key criteria: shipped a language-model feature to real users (support team), built and maintained an evaluation suite with quality gates (0.85 threshold), automated an end-to-end workflow (manual triage to 5-minute handling), owned production systems with on-call responsibilities, shows substantial Python experience, handled multilingual/messy d
- **dev-002** → insufficient_information: The document provides only a high-level summary of experience without specific details about any of the key criteria. There is no concrete evidence of shipped language model features, quality measurement practices, production system ownership, workflow automation, or work with messy/sensitive data.
- **dev-003** → decline: The candidate explicitly states they have never worked on evaluation or measurement of model quality, which is a weighted requirement (weight 5) for this role. While they demonstrate Python experience, ownership of production systems (on-call), and some measurement practices (evaluation suite), the absence of language model experience and their explicit disclaimer about model quality work indicate
- **dev-004** → insufficient_information: The resume provides minimal details about Priya's work. While it mentions owning a billing service, on-call rotation, and building a reconciliation pipeline in Python, there is no evidence of language model experience, quality measurement practices, workflow automation, or work with sensitive/messy data—all critical for this role. The document lacks sufficient depth to make a confident assessment 
- **dev-005** → hold: The candidate demonstrates strong production ownership (on-call, deploys, incident response) and substantial Python experience with a long-lived system, but the documents provide no evidence of shipping language-model features, measuring quality systematically, or working with messy/sensitive data—all significant requirements for this role. A second opinion is warranted to clarify whether their pr
- **dev-006** → insufficient_information: The document confirms Python experience, production ownership, and latency optimization work, but provides almost no detail on the nature of these systems, whether language models were involved, quality measurement practices, workflow automation, cost considerations, or data handling. The resume is too sparse to assess most role requirements.
- **dev-007** → hold: The candidate demonstrates production ownership (on-call, payments systems) and substantial Python experience with real systems, but the document lacks evidence of language model features, quality measurement practices, or end-to-end workflow automation. Critical role requirements are either absent or unclear, warranting further investigation.
- **dev-008** → insufficient_information: While the document lists relevant keywords and skills that align with the role requirements, it provides no concrete examples, projects, or evidence demonstrating actual accomplishment of these capabilities. The document appears to be a template or summary rather than substantive evidence of shipped systems, measured quality, automated workflows, or production ownership.
- **hold-101** → advance: Sofia demonstrates shipping a language-model feature to real users (clinical summarization tool used by ~200 nurses), measures quality before scaling (built 400-example evaluation set and harness that blocked releases), runs what she builds (carries pager since launch), and has substantial Python experience. She also states UK work eligibility, meeting most critical requirements with strong eviden
- **hold-102** → insufficient_information: The document provides only high-level role titles and vague descriptions without concrete evidence of the specific competencies being evaluated. There is no detail about language models, measurement practices, production ownership, data handling, costs, cross-functional communication, or work location eligibility.
- **hold-103** → insufficient_information: The resume provides minimal technical detail about the candidate's work. While it mentions owning a fleet telemetry service and building an ingestion pipeline in Python, there is no information about language models, quality measurement, automation of workflows, production operations, data handling practices, cost considerations, or technical communication—most of which are core requirements for t
- **hold-104** → decline: The candidate's experience is entirely in structural engineering and civil infrastructure with no evidence of work with language models, machine learning systems, Python, or software engineering. This role requires shipping language-model features and building automated workflows, which are not present in this candidate's background.
