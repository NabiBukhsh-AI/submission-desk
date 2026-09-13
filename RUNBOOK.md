# Runbook

How to set up, run, and fix Submission Desk. Written for the person on the
afternoon something is wrong, so each section starts with the command and ends
with what to do when it does not work.

## 1. Setup

Requirements: Python 3.11 and `make`. On Windows, PowerShell and Git Bash
both work for every target except `make clean`, which uses `rm` and `find`.
Tesseract is optional and only needed for scanned documents.

    git clone <repository> submission-desk
    cd submission-desk
    make setup          # .venv, dependencies, the submission-desk command
    make seed           # synthetic candidates, assessed offline
    make doctor         # says what is and is not configured
    make demo           # the reviewer interface, demo mode on

`make setup` creates `.venv/` and installs the project in editable mode, which
registers the `submission-desk` command inside it. Activate the environment
(`source .venv/bin/activate`, or `.venv\Scripts\activate` on Windows) to use it
directly; every Makefile target already uses the venv's interpreter.

`make check` runs everything CI runs: both scanners, the linter and formatter,
the type checker, and the offline test suite. It takes a few minutes and needs
no network.

The React interface (optional; the Streamlit one needs nothing more):

    make api            # the HTTP API on :8000, demo mode on
    make web            # Vite dev server on :5173, proxying /api to the API

Node 20+ is required for `make web`; `npm install` runs on first use. Without
demo mode, run the API as `submission-desk api` with your `.env` in place. For
a static deployment, `cd frontend && npm run build` produces `frontend/dist/`,
and `VITE_API_BASE=https://your-api.example` at build time points it at the
API. The API has no authentication; see section 7.

Docker, for parity only:

    docker build -t submission-desk .
    docker run --rm -p 8501:8501 submission-desk

## 2. Configuration

Copy `.env.example` to `.env` and fill in what you need. Every variable in the
example is read by the code and documented beside its name; the defaults run
the offline system. Three things decide what a deployment is:

| Setting | Offline default | A pilot |
|---|---|---|
| `MODEL_PROVIDER` | `fake` — recorded fixtures, then the deterministic stand-in | `anthropic` or `openrouter`, with the key and tier bindings — set on the admin page or in the environment (section 6) |
| `DEMO_MODE` | off | off. On, the system reads only the synthetic corpus and cannot send anything |
| `REVIEWER_ID` | unset — nothing can be approved | the reviewer's identifier, because every decision is attributed |

Real candidate documents live outside the repository, at a path you choose,
and are never committed. `PILOT_DATA_DIR` documents where; `INBOX_DIR` points
the local source at a folder. Both are refused while `DEMO_MODE` is on.

Limits that are not environment variables — page counts, document sizes, OCR
triggers — are in `config/limits.yaml`. Tier bindings are in
`config/models.yaml`. Prices are in `config/pricing.yaml` and ship empty.

### The admin account

The HTTP API and the React frontend are behind one admin account. There is
nothing to configure: the first person to open the frontend against a fresh
database is asked to create the account (any username, a password of at least
ten characters), and is signed in. After that the same form signs in. The
password is stored as a scrypt hash; the session is a signed cookie that lasts
twelve hours. There is one account and no way to create a second one from the
interface; a lost password is reset by deleting the `admin_username` and
`admin_password_hash` rows from the `settings` table, after which the next
visitor creates the account again.

**Settings** in the top bar is the admin page: the provider, its API key, the
model per tier and its prices, the reviewer id, blind mode and retention. What
is saved there is laid over the environment and takes effect on the next
request, no restart. Keys are sealed with `APP_SECRET` before they are written
and are never sent back to a browser — the page shows that a key is set, not
what it is. `APP_SECRET` is generated once beside the database when it is not
set; set it explicitly for any deployment beyond one laptop, and know that
changing it makes every stored key unreadable (re-enter them on the page).

## 3. Operate

### Process candidates

Through the interface: **Upload**, drag the files, check the grouping, process.
From the command line, from a folder:

    submission-desk process --role ai-engineer            # everything in the inbox
    submission-desk process --role ai-engineer --limit 10
    submission-desk process --role ai-engineer --force    # re-run finished ones

The local source reads one subfolder per candidate, or loose files grouped by
the prefix before the first `_` or `-`. A candidate already processed under
the same documents and configuration is reused, not re-run; `--force` starts a
fresh run.

### Review

**Queue** opens on the candidates waiting for somebody. **Review** shows the
integrity banner, the recommendation and its derivation, the evidence per
criterion, the quotations the system rejected, and the suggested questions.
Correct a criterion and say why; approve, ask for more, or do not proceed.
[docs/RECRUITER_GUIDE.md](docs/RECRUITER_GUIDE.md) is the one-page version.

### Deliver

Approval records a decision. Sending is a separate step:

    submission-desk deliver                 # every approved package
    submission-desk retry-deliveries        # where a destination failed last time

Delivery writes the CSV first, always, then each configured sink. A run where
some sinks failed is `delivery_pending_retry`, with a record per sink saying
which, and `retry-deliveries` sends only to the ones that failed. In demo mode
both commands say that nothing is sent and leave the run approved.

### Housekeeping

    submission-desk reconcile               # stalled runs become resumable
    submission-desk purge                   # preview what retention would remove
    submission-desk purge --yes             # remove it
    submission-desk purge --days 7 --yes    # a shorter period, once

`reconcile` runs at interface startup as well. `purge` removes finished runs
older than `RETENTION_DAYS`, every row recorded against them, and their
document bytes unless another run still refers to them. It previews by
default; nothing is removed without `--yes`. Nothing schedules it.

### The rubric

`rubrics/ai-engineer.yaml`, or the **Rubric** page. `make rubric-lint`
validates every rubric and prints its hash. Candidates already assessed keep
the rubric hash they were assessed under; the next run uses the new one.

## 4. The doctor, and each thing it can say

    submission-desk doctor

Reads configuration and the local system. It never contacts a provider. Each
line is `[ ok ]`, `[warn]` or `[fail]` with an action; a failure exits 1.

| Check | It fails or warns when | What to do |
|---|---|---|
| database | the file does not exist | `make setup` creates it and applies migrations. A missing database is a warning: the first run creates it. |
| migrations | the database is behind the code | `make setup` applies the outstanding migrations. Back up the file first if it holds real data. |
| migrations | the database is *ahead* of the code | Somebody ran a newer revision against this file. Check out that revision, or move the file aside and start fresh. Do not downgrade in place. |
| prompts | `prompts/` is missing or empty | Restore it from version control. The registry refuses to start without every declared prompt. |
| rubrics | no YAML under `rubrics/`, or one does not validate | `make rubric-lint` names the field. Nothing can be assessed without a valid rubric. |
| model provider | `fake` | A warning, not a failure: the demo and the suite run this way. Set `MODEL_PROVIDER=live` for real candidates. |
| model provider | `live` with no transport wired | Section 6. The system will refuse at the first call until one is. |
| pricing | `config/pricing.yaml` has no rates | A warning. Every cost reads "not configured" until rates are entered; token counts are recorded regardless. |
| OCR | Tesseract is not on `PATH` | Scanned documents will be refused rather than read. Install it, or set `TESSERACT_BINARY`. Plain-text and digital PDFs are unaffected. |
| document source | the folder does not exist yet | Uploading creates it. `make seed` creates the synthetic one. |
| disk space | under 500 MB free where documents are stored | A failure. Free space before processing anything: intake refuses on a full disk rather than corrupting a document, and 500 MB is the margin before that happens. |
| reviewer | `REVIEWER_ID` is unset | Set it before anybody reviews. Runs process without it; nothing can be approved. |
| demo mode | on | Nothing is sent anywhere and only the synthetic corpus is read. Expected for the demo; unset it for a pilot. |
| demo mode | on, with a real document path configured | The system refused to start. Unset `PILOT_DATA_DIR`, `INBOX_DIR`, or `SOURCE_ADAPTER`, or unset `DEMO_MODE`. Both at once is the one configuration that will not run. |
| credential | a variable is unset | Optional integrations. Presence is checked; the value is never printed. |

## 5. When something is wrong

**Watch it work.** `make api` and `make api-live` print one line per node and
per model call as they happen:

    07:45:22  model.call       site=structure.profile tier=tier_cheap model=… tokens=1316/1740 ms=17483 ok=True
    07:45:27  model.call       site=assess.criterion tier=tier_cheap model=… tokens=1457/89 ms=3953 ok=False problem=evidence.0: … must quote the document
    07:45:27  model.repair     site=assess.criterion why=evidence.0: …
    07:45:56  node.assess      run_id=01a099ba candidate=x status=ok to=assessed ms=33178

`ok=False` with a `problem` is the model's answer failing the contract; the
repair line is the one retry. The same events go to `data/logs/events-<date>.jsonl`
in full, redacted. Set `LOG_CONSOLE=1` for the same lines from any command.

**The queue shows "Could not finish".** Open the candidate; the reason is on
the run. Common ones: a document refused at intake (encrypted, corrupt, too
large, almost no text), extraction that found nothing, or the token ceiling.
The documents are still there to read. Fix the cause and process again with
`--force`.

**A run is stuck in processing.** The process died mid-run. Restart the
interface, or run `submission-desk reconcile`: runs untouched for longer than
`STALE_RUN_MINUTES` become resumable, and the next process picks up from the
last completed node.

**"Somebody else changed this candidate while you had it open."** Two reviewers
decided the same run. The first write won. Reload the page, read their
decision, and decide whether yours still applies.

**A candidate was quarantined.** A document contained content aimed at an
automated reader. The flagged text is quoted on the review page. Nothing was
assessed and nothing was spent. A person reads the document and decides; the
run reaches a decision only through review.

**Every candidate is coming back "needs a closer look".** Check the reasons on
the review page. If it is the amber integrity tier on ordinary CVs, a detector
is firing on legitimate writing: note the detector id from the banner and
raise `SUSPECT_CONFIDENCE` only after reading what it matched. If it is the
coverage gate, the rubric asks for more than the documents typically say;
lower `min_coverage` in the rubric deliberately, or accept that the system is
asking for more information.

**Cost figures all read "not configured".** They will until rates are entered
in `config/pricing.yaml`. This is not an error. The token counts beside them
are recorded.

**A delivery failed.** `submission-desk retry-deliveries`. The message names
the sink and the cause. An authentication or configuration failure disables
that sink for the session rather than failing every candidate the same way;
fix the credential or the setting, restart, retry. A transient failure is
retried automatically with backoff, and again by the command.

**The database file is corrupt or lost.** Documents are stored by content hash
under `BLOB_DIR` and the CSV of delivered results is under
`data/deliveries/`. Runs, evidence and decisions are only in the database.
Restore from backup; there is no other copy of a decision.

## 6. The model provider

Two providers are wired, and the offline stand-in is the third:

| Provider | Key from | Models the admin page offers | Cost |
|---|---|---|---|
| `anthropic` | console.anthropic.com → API keys (`sk-ant-…`) | `claude-haiku-4-5` ($1 / $5), `claude-sonnet-5` ($2 / $10), `claude-opus-5` ($5 / $25) per million input / output tokens | paid |
| `openrouter` | openrouter.ai/keys (`sk-or-…`) | `inclusionai/ling-3.0-flash-fin:free`, `dots-studio/dots-3-note-preview:free` | free tiers, rate-limited |
| `fake` | — | — | nothing |

The prices are what the vendors published when this was written; the fields
on the admin page are editable because they change. Any other model id the
provider serves can be typed in.

To run against a real provider, on the admin page (or in the environment):

1. Choose the provider and paste its key. The key is sealed before it is
   stored.
2. Pick a model per tier. The cheap tier reads every document; the strong tier
   handles criteria the rubric marks high-stakes. A preset fills the model id
   and both prices; zero is a real price for a free model.
3. Save, then **Test the connection**. That makes one tiny structured call and
   reports what the provider said and what it cost — the only time the
   system contacts a provider on purpose without a candidate.
4. Start the API without demo mode (`make api-live`) so uploads are accepted.
   `make api` is demo mode: the stand-in answers whatever the page says.

The free OpenRouter models are usable and slow to be trusted: they think
before answering (reasoning is switched off in the request, or the whole
output budget goes on the thinking), they do not all take a JSON schema (the
request is retried as plain JSON), and they often mark a criterion supported
without quoting the document, which the contract refuses and the one repair —
sent without the document, by design (ADR-004) — cannot add. Such a criterion
ends as insufficient evidence rather than a made-up quote. A CV through Claude
Haiku 4.5 costs about three cents and follows the contract.

Structured output is asked for as a JSON schema on both providers. OpenRouter's
free models do not all honour `response_format`; when one refuses, the same
request is sent once more with the schema described in the prompt, and the
answer is validated against the contract either way. Every provider failure —
a rejected key, no credit, a rate limit, a refusal — reaches the queue as a
sentence saying what to do, never a traceback.

A third provider is one file: a `make_transport(api_key, base_url)` in
`infrastructure/models/transports/` returning `transport(payload, *, timeout)
-> {"id", "text", "usage": {"input_tokens", "output_tokens",
"cached_input_tokens"}}`, registered in `PROVIDERS` in
`infrastructure/factory.py`. Usage must be what the provider reported, never
estimated. `tests/unit/test_provider_transports.py` shows what to assert.

**When the provider is down.** Runs fail at the first model call with a
retryable error and land in the queue as "Could not finish". Nothing is lost:
intake, extraction and sanitisation are committed and are not repeated. When
the provider is back, process again with `--force`; the completed nodes are
skipped and only the model calls happen. Reviewing and delivering already-
assessed candidates does not touch the provider and keeps working throughout.

**When it is slow or rate-limited.** The client does not retry a slow call —
that would double the bill for the same answer. Lower `ASSESS_CONCURRENCY`
to reduce parallel calls per candidate. The token ceiling still applies.

## 7. Data handling

Nothing under `data/` is committed except `data/samples/`, and the PII scanner
refuses a commit that tries. Logs go through a redactor before any sink: no
email, no phone number, no quotation unless `LOG_SPANS` is on, and never a
credential. Real documents belong outside the repository at `PILOT_DATA_DIR`
and are removed by `purge` after `RETENTION_DAYS`.

There is no authentication on the interface. Run it on one machine for one
reviewer, or put an identity layer in front of it. See
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## 8. Evaluation

    make eval               # dev split, gated against the pinned baseline
    make eval-holdout       # the held-out split; reported, not gated
    make eval-routing       # three routing policies
    make eval-calibration   # calibration off and on
    make eval-fairness      # counterfactual pairs, with the control arm
    make eval-accept        # pin the current dev result as the baseline
    make tune-thresholds    # the span-threshold sweep

`make eval` exits 1 if a gated metric moved beyond its tolerance and names the
cases that changed. Read the cases before accepting a new baseline. What each
number means, and which are absent, is in
[docs/EVALUATION.md](docs/EVALUATION.md).

Reviewer corrections become draft cases with
`python -m scripts.overrides_to_cases`; each draft needs a synthetic document
and a decision on the band before it can join the suite.
