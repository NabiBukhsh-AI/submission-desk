# Limitations

What this system gets wrong, cannot do, or has not shown. Written to be read
before the README's claims are believed, and not softened: a submission with no
stated failures reads as unexamined, and a screening system whose limits are
unknown is one whose users will discover them on a candidate.

Where a limit has a number, the number is in
[docs/EVALUATION.md](docs/EVALUATION.md). Where it does not, that is the limit.

## Things that were not measured

**No human baseline, so no productivity claim.** The design anticipated a
timed session in which a recruiter screened ten to twelve candidates by hand,
producing both the gold labels and the baseline for every "faster than" and
"agrees with" figure. That session did not take place. Arm A is reported as
absent in every table, and the productivity dip the design expected during
the first days of use — the one a pilot exists to observe — was not observed,
because there was no pilot. The mechanism is still built: elapsed seconds and
a trust rating are recorded on every decision, so the first real week of use
produces the numbers this document lacks.

**No model was measured.** Every figure in the evaluation comes from a
deterministic stand-in that quotes the document literally. The numbers say
whether the pipeline does the right thing with evidence; they say nothing about
whether a language model finds the right evidence, paraphrases correctly, or
resists an instruction that the deterministic detectors miss. Point
`MODEL_PROVIDER` at a real provider and the dev and holdout suites measure the
model. The fairness harness does not: it is wired to the stand-in on purpose,
so that a flip is the system changing its mind rather than a model sampling
differently, and measuring a model there means wiring the provider client with
the response cache off. That is a code change, not a flag.

**No cost in money.** Prices ship empty. The routing experiment that would
justify the routed policy ran and produced identical numbers in all three arms,
because the stand-in never gives it a reason to escalate. The cost column reads
*not measured*, and the claim that routing saves money is unsupported until a
provider is priced.

**Calibration.** Implemented, ablated, and the ablation showed nothing over an
index that was empty. It ships off. A further limit: a calibration card written
at delivery is built from the state available then, and the structured profile
is not persisted beyond the run, so cards written after a review carry the
criterion states and a summary saying no structured history was available.
That would weaken retrieval for a system that turned the feature on, and it
is one more reason it is off.

**One labeller.** All twelve gold labels were written by one person. The
agreement figures inherit that reading of the rubric, and there is no inter-
annotator figure to say how much of the disagreement is the system's.

## The benchmark is small and the corpus is synthetic

Twelve cases. Two of them assert a band, so band accuracy has n=2 and its
interval is nine to ninety percent. That is not a defect in the arithmetic; it
is the honest width of a claim on two observations, and the reason most cases
were written to assert abstention or quarantine instead.

Every document in the repository is invented. Synthetic CVs are cleaner than
real ones — consistent dates, one column, no headers repeated on every page, no
scanned coffee stain — which flatters extraction. Messy cases were written
deliberately (a scanned page, two languages, a contradiction, keyword stuffing)
but a written-to-be-messy document is still tidier than the real thing.
Extraction quality on real documents is unmeasured.

The stand-in's one known failure is on the record: it matches words and does
not stem, so "eligible to work in the United Kingdom" does not satisfy a
criterion about "eligibility", the blocker resolves unknown, and the strongest
candidate on the dev split lands in manual review instead of advance. The
number was left as it fell rather than tuned, because tuning the stand-in
would raise the figure without making the harness measure anything more.

## Known failure modes

**A document that contains the right sentences.** The keyword-stuffed case
reached manual review, not a band, because coverage and the blocker rule
stopped it. But to a quoting system, a CV that *contains* "built an evaluation
suite that gated every release" is indistinguishable from one written by
somebody who did. Span validation proves the sentence is in the document, not
that it is true. This is the limit of evidence-first screening, and a person
has to read the document. The system says so; it does not solve it.

**Fabrications one character from the truth.** The threshold sweep shows it: at
the shipped digital threshold, 0 of 10 fabricated spans were accepted, but the
fabricated set is small and none of its members was "BSc" changed to "MSc". No
similarity threshold separates that from an OCR misread of the same size. The
OCR threshold accepts 2 of 10 fabrications at the operating point that keeps
15 of 16 real quotations. Fuzzy matching is one of four controls, and a
reviewer who trusts a fuzzy-matched span without clicking through to the
source is trusting the wrong thing.

**Multi-column layouts.** Reading order is reconstructed from block geometry
with a heuristic. Two-column CVs with a sidebar can interleave; a span that
crosses a block boundary is flagged rather than fixed. No layout model.

**Scanned documents.** OCR runs when the text layer is thin, with a per-page
confidence, and a low-confidence page is flagged to the reviewer rather than
retried at higher resolution. Poor scans produce poor evidence, visibly.
Tesseract is a system binary; without it, scans are refused rather than read.

**No table reconstruction.** Tables in PDFs are read as text in whatever
order the extractor yields cells. A skills matrix or a dated table of roles
may come out scrambled, and evidence quoted from it will be a fragment. DOCX
tables are read row by row, which is better and still not a table.

**No translation, and no routing by language.** Languages are detected per
page and recorded on the document, and that is all that happens with them.
Nothing is translated and nothing is routed to a person because of its
language, so a criterion answered only in a language the model reads poorly
resolves as insufficient evidence — correct, and unhelpful, and invisible
unless the reviewer notices the language column.

**Instruction injection the detectors do not recognise.** Ten deterministic
detectors, each with true-negative tests so they do not fire on ordinary
writing. That is also their limit: an instruction phrased in a way none of
them matches reaches the model. The remaining controls — the nonce-fenced
region, the data-not-instruction sentence, a response schema with no field an
instruction could fill, span validation, and the person at the end — are what
hold then, and how well the model itself resists is unmeasured.

**Contradictions are routed, not resolved.** A CV and a cover letter that
disagree produce a criterion that scores nothing and a run that needs a
person. That is the design, and it means a candidate with one inconsistent
date gets a slower answer than one with none.

## Redaction has leakage channels

Blind mode removes names, pronouns, and contact details before the model sees
the text. It does not remove:

- **Employer and institution names**, which correlate with nationality and
  class and are exactly the evidence a rubric asks for.
- **Dates**, which reveal age. A graduation year is a birth year plus twenty-
  two.
- **Places** in a work history, which reveal where somebody has lived.
- **Language and register**, which correlate with a first language.
- **Gaps** and their explanations, which correlate with caregiving and health.

The protected-attribute matcher drops a *claim* that names an attribute; it
cannot drop an inference the model made without naming it. The fairness
experiment varies name, pronoun and email only, so it measures the channel
that redaction closes and none of the ones it does not.

The log redactor removes emails, phone numbers and the span fields, and is
tested in both directions. It does not remove a person's name from free text,
because a name is not a pattern, so a log line that quotes a candidate's
sentence with their name in it would carry the name. Spans are dropped from
logs by default for that reason, and switching them on is a deliberate act.

## Operational limits

**No authentication.** The interface has no login. Anybody who can reach the
port can review, approve, and read every candidate. Decisions are attributed
to a configured reviewer id, not to a verified person. This is acceptable for
one recruiter on one machine and for nothing else; a hosted deployment needs
an identity layer in front of it and this repository does not provide one.

**Single writer.** SQLite in WAL mode. One process writes; a second concurrent
reviewer works until two of them decide the same candidate in the same second,
and then one of them is told to reload. Sustained write contention would show
up as timeouts, not corruption, and it is the trigger for the storage decision
to be revisited.

**One machine.** No queue, no worker pool, no horizontal anything. Candidates
are processed sequentially; criteria within a candidate in parallel. Tens of
candidates an hour, not thousands.

**No provider transport ships.** The live client is built and its failure
paths are tested against a scripted transport, but the function that performs
the HTTP call to a specific vendor is deployment configuration and is not in
the repository. Setting `MODEL_PROVIDER=live` without wiring one fails the
doctor and refuses at the first call, with a sentence saying so.

**No fixture recording tool.** Recorded responses are replayed if present under
`tests/fixtures/llm/`; there is no command that records them from a live run.

**Delivery is a separate step.** Approving a candidate records the decision;
sending the package is `submission-desk deliver` or the queue's action. A
reviewer who expects approval to send will find the run still marked approved.

**Retention is manual.** `purge` exists and previews by default; nothing runs
it on a schedule.

## What would change these

In rough order of value: a recruiter session, which produces the baseline, the
labels, and the productivity number in one afternoon; a second labeller; a
priced provider, which turns three "not measured" rows into numbers in one
run; and real documents in a private pilot, which is the only way to learn how
much the synthetic corpus flattered extraction.
