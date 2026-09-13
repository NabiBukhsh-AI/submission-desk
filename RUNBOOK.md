# Runbook

How to set up, run, and fix Submission Desk. Written for the person on the
afternoon something is wrong, so each section starts with the command and ends
with what to do when it does not work.

## 1. Setup

Requirements: Python 3.11, Node 20+ and `make`. On Windows, PowerShell and
Git Bash both work for every target except `make clean`, which uses `rm` and
`find`. Tesseract is optional and only needed for scanned documents.

    git clone <repository> submission-desk
    cd submission-desk
    make setup          # .venv, dependencies, the submission-desk command
    make seed           # synthetic candidates, assessed offline
    make doctor         # says what is and is not configured
    make api            # the HTTP API on :8000, demo mode on
    make web            # in a second terminal: the interface on :5173

Open http://localhost:5173. The first visit creates the one admin account
(any username, a password of at least ten characters); after that the same
form signs in. The queue shows the four synthetic candidates.

`make setup` creates `.venv/` and installs the project in editable mode, which
registers the `submission-desk` command inside it. Activate the environment
(`source .venv/bin/activate`, or `.venv\Scripts\activate` on Windows) to use it
directly; every Makefile target already uses the venv's interpreter.

`make check` runs everything CI runs: both scanners, the linter and formatter,
the type checker, and the offline test suite. It takes a few minutes and needs
no network.

`make web` runs `npm install` on first use. `make api` is demo mode: only the
synthetic corpus is read, uploads are switched off, nothing can be delivered.
`make api-live` starts the API on real documents (section 3). Both print one
line per node and per model call as they run (section 5).

The earlier interface, `make demo`, is a Streamlit page over the same use
cases. It has no login and stays for one person on one machine.

One container for the whole thing — the API, the built interface served from
the same origin, and Tesseract — is section 6a:

    docker build -t submission-desk .
    docker run --rm -p 8000:8000 -e APP_SECRET=change-me submission-desk

## 2. Configuration

Copy `.env.example` to `.env` and fill in what you need. Every variable in the
example is read by the code and documented beside its name; the defaults run
the offline system. Three things decide what a deployment is:

| Setting | Offline default | A pilot |
|---|---|---|
| `MODEL_PROVIDER` | `fake` — recorded fixtures, then the deterministic stand-in | `anthropic`, with the key and tier bindings — set on the admin page or in the environment (section 6) |
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

To skip the setup visit — a hosted copy, or a machine that is reinstalled —
set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in the environment: the account
exists with those credentials before the first request, and if the stored
account ever differs (a wiped disk, a rotated password) it is brought back
into line at start. A password under ten characters is refused, with a line
in the log, and the setup form appears instead.

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

Three roles ship as files in `rubrics/`: `ai-engineer`, `fullstack-developer`
and `flutter-mobile-developer`. `make rubric-lint` validates every file and
prints its hash. Candidates already assessed keep the rubric hash they were
assessed under; the next run uses the new one.

**Roles** in the React interface shows each role as it is configured — every
requirement with its question, kind, weight, the number of quotations it
needs, and its examples; the coverage gate; the bands — and lets the admin
edit it, add or remove requirements, or duplicate it as a new role. The whole
rubric is validated before anything is stored (the same rules as the lint),
so a half-edited role can never be run. A saved rubric lives in the database
and wins over the file of the same name; **Reset to file** returns to the
shipped version. "Show as YAML" renders the same rubric in the file's form.

### Deleting a candidate

Select candidates in the queue and **Delete**, or **Delete** on the review
page. The run and every row recorded against it go; a document goes with it
unless another run still refers to it. Two refusals: a run the pipeline is
still working on (wait, or let the reconciler mark it interrupted), and the
sample candidates that ship with the system, which are recognised by the
content hashes of their documents and shown with a *sample* badge. The
retention purge (`submission-desk purge`) is the scheduled form of the same
removal.

### Running candidates again

In the queue, select candidates and choose a role: their stored documents go
through the pipeline for that role, without uploading again. The same
documents against the same role and configuration are recognised as already
done rather than repeated; a different role, or a changed rubric, is a new
run with its own hash.

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
| model provider | `fake` | A warning, not a failure: the demo and the suite run this way. Choose Anthropic on the Settings page (or set `MODEL_PROVIDER=anthropic`) for real candidates. |
| model provider | `anthropic` with no key | A failure. Save the key on the Settings page, or set `MODEL_API_KEY`; until then the stand-in answers every call. |
| pricing | no rates on the Settings page or in `config/pricing.yaml` | A warning. Every cost reads "not configured" until rates are entered; token counts are recorded regardless. |
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

One provider is wired — Anthropic, through its own SDK — and the offline
stand-in is the other:

| Provider | Key from | Models the admin page offers | Cost |
|---|---|---|---|
| `anthropic` | console.anthropic.com → API keys (`sk-ant-…`) | `claude-haiku-4-5` ($1 / $5), `claude-sonnet-5` ($2 / $10), `claude-opus-5` ($5 / $25) per million input / output tokens | paid |
| `fake` | — | — | nothing |

The prices are what Anthropic published when this was written; the fields on
the admin page are editable because they change. Any other `claude-…` model
id can be typed in; anything else is refused on save, because a model the
provider does not serve fails every call with a 404.

To run against the provider, on the admin page (or in the environment):

1. Paste the key. It is sealed before it is stored.
2. Pick a model per tier. The cheap tier reads every document; the strong tier
   handles criteria the rubric marks high-stakes and the escalations. A preset
   fills the model id and both prices. Haiku 4.5 on both tiers is the cheap,
   good setup — a CV costs a few cents.
3. Save, then **Test the connection**. That makes one tiny structured call
   per tier and reports what the provider said and what it cost — the only
   time the system contacts the provider on purpose without a candidate.
4. Start the API without demo mode (`make api-live`) so uploads are accepted.
   `make api` is demo mode: the stand-in answers whatever the page says.

Structured output is asked for as a JSON schema (`output_config`), so the
first text block is JSON matching the contract; responses are streamed so a
long profile does not trip a request timeout. Every provider failure — a
rejected key, a rate limit, an unknown model, a safety refusal — reaches the
queue as a sentence saying what to do, never a traceback.

A second provider is one file: a `make_transport(api_key, base_url)` in
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

## 6a. Hosting on Render

One container: the API, the built interface served from the same origin, and
Tesseract. `render.yaml` is the blueprint.

1. Push the repository. In Render: **New → Blueprint**, pick the repository,
   accept the service it finds. It asks for the two values marked
   `sync: false`: `ADMIN_PASSWORD` (ten characters or more) and
   `MODEL_API_KEY`. The first build takes about five minutes.
2. Open the service URL and sign in as `admin` with that password. The
   reviewer id, the provider and the models are already set from the
   environment; **Settings → Test the connection** confirms the key.
3. Upload a CV.

What the free plan does and does not do: the service sleeps after fifteen
minutes idle (the first request wakes it, slowly) and its filesystem is
wiped on every deploy — the database and uploaded documents start over; the
sample candidates are re-created at start. Because the account, the reviewer
and the key come from the environment, nothing has to be re-entered. For
data that survives deploys, a paid plan with the disk block in `render.yaml`
uncommented: the paths already point at `/var/data`.

The same image runs anywhere Docker does:

    docker build -t submission-desk .
    docker run --rm -p 8000:8000 -e APP_SECRET=change-me -v sd-data:/app/data submission-desk

The container starts without demo mode: uploads are accepted and assessed by
the stand-in until a key is saved. To run the hosted copy in demo mode
instead, set `DEMO_MODE=true` in the service's environment.

## 7. Data handling

Nothing under `data/` is committed except `data/samples/`, and the PII scanner
refuses a commit that tries. Logs go through a redactor before any sink: no
email, no phone number, no quotation unless `LOG_SPANS` is on, and never a
credential. Real documents belong outside the repository at `PILOT_DATA_DIR`
and are removed by `purge` after `RETENTION_DAYS`.

The HTTP interface is behind the one admin account (section 2); the
Streamlit page has no login and is for one person on one machine. What is
and is not defended against is in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## 8. Evaluation

    make eval               # dev split, gated against the pinned baseline
    make eval-holdout       # the held-out split; reported, not gated
    make eval-routing       # three routing policies
    make eval-calibration   # calibration off and on
    make eval-fairness      # counterfactual pairs, with the control arm
    make eval-baseline      # the naive one-call baseline against the pipeline, on the real model
    make eval-accept        # pin the current dev result as the baseline
    make tune-thresholds    # the span-threshold sweep

`make eval` exits 1 if a gated metric moved beyond its tolerance and names the
cases that changed. Read the cases before accepting a new baseline. What each
number means, and which are absent, is in
[docs/EVALUATION.md](docs/EVALUATION.md).

Reviewer corrections become draft cases with
`python -m scripts.overrides_to_cases`; each draft needs a synthetic document
and a decision on the band before it can join the suite.
