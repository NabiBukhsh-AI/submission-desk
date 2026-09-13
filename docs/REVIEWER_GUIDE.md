# Using Submission Desk

For the person doing the screening. No technical background assumed.

## What this does

It reads a candidate's CV against a list of things the role needs, and shows you
what the documents actually say about each one — quoted, with the page number.

It does not decide anything. It produces a recommendation and the reasoning
behind it, and you decide. Nothing is sent to a candidate until you approve it.

## What it will not do

It will not tell you someone is a good fit. It will tell you what their
documents say about the role's requirements, and what a fixed set of rules
makes of that.

It will not guess. If a CV never mentions evaluation work, the system says the
CV never mentioned it — not that the candidate cannot do it, and not that they
scored badly on it.

It will never mention age, gender, nationality, ethnicity, religion, marital or
family status, disability, personality, culture fit, or appearance. If you see
any of those anywhere in this system, that is a bug worth reporting immediately.

## Getting started

Open the address you were given (locally, http://localhost:5173 after
`make api` and `make web`). The first person to open a fresh installation is
asked to create the admin account: any username, a password of at least ten
characters. After that the same form signs in. Sessions last a working day.

It works on a phone: the navigation moves to the bottom of the screen.

## The pages

### Home

What the system does and what it defends against, in one page. Public; no
sign-in needed to read it.

### Queue

Everything that has been processed, by what is waiting on you. It opens on
**Needs attention**. The other filters — waiting on a candidate, decided, in
progress, everything — are there when you want them.

The chips mean:

| Chip | What it means |
|---|---|
| ✅ Ready to review | Everything checked out. Open it when you have a moment. |
| ⚠️ Needs a closer look | Something wants your judgement. The page says what. |
| 🛑 Documents flagged | A document contained content aimed at an automated reader. Nothing was assessed and nothing was spent. |
| ✉️ Waiting on the candidate | You asked for more information. Nothing to do until it arrives. |
| ⚠️ Could not finish | A step failed. The documents are still there to read. |
| 👍 / 👎 | You have decided. |

Tick candidates to act on several at once: **Run on this role** sends their
stored documents through again against another role (nothing is uploaded
twice; the same documents on the same role are recognised, not repeated), and
**Delete** removes the runs and any document nothing else uses. The four
sample candidates that ship with the system are marked *sample* and cannot be
deleted.

### Upload

Choose the role, then the files: PDF, Word or plain text, up to 25 MB and eight
documents per candidate. The system guesses which files belong to the same
person and shows you the grouping so you can correct it — filenames are how
people name things, not how systems identify them, and it will sometimes get
this wrong. Progress shows in the queue; a candidate takes about half a minute.

### Review

The page is ordered by the questions you will actually ask, and every word on
it is defined — hover a term, or open **What every word on this page means** at
the bottom.

**Can I trust these documents?** The banner at the top. Green means nothing was
found. Amber means something was flagged and the assessment continued anyway.
Red means a document contained content designed to manipulate an automated
reader — the flagged text is quoted in full so you can see exactly what it was.

**What is the outcome, and why?** The band with what it means, then seven
facts with their definitions: coverage against this role's gate, the score (or
the provisional score when a band was withheld), requirements assessed,
quotations verified and excluded, what the run cost, and how many repairs and
escalations it took. If it says **Not enough information**, that is not a low
score: the documents covered less of the role than the gate requires, and the
questions further down say what to ask for. If it says **Check with the
candidate first**, a requirement that decides the outcome on its own — work
authorisation, typically — was not answered either way, and the system will
not decline on silence.

**Why does this need a person?** Each reason, with what it means and what to
do about it. None of them change the band.

**How was this worked out?** Every rule that fired, in order, with its inputs
and what it produced. Nothing in it came from a model; the band can be traced
by hand.

**Point by point.** A table of every requirement: its kind (standard,
high-stakes, blocker), its weight, the quotations found against the number
needed, its state, and whether it counted toward coverage.

**What did it read?** Each requirement, expandable, with the quotations behind
it, how each quotation was checked, and **Show me where**, which puts the
quotation back in its surrounding paragraph on the page it came from.

**What did it throw away?** The excluded panel. These are quotations the system
could not find in the source document — the system checking its own work and
catching itself. An empty panel is good. A full one is worth reading.

**What should I ask next?** Suggested questions and information requests, each
one derived from a point the documents did not settle.

**Eligibility** is collapsed, at the bottom, on purpose.

### Correcting an assessment

If the system got a point wrong, open **Correct an assessment**, change what the
point should have resolved to, and say why.

You never type a score. Changing a point recalculates the recommendation using
the same rules the system used in the first place, and you see the new
recommendation before you commit to anything.

The "why" matters more than it looks. Each reason routes somewhere different:

- *The CV says this somewhere the system did not find* — improves how it reads.
- *The quotation does not match the document* — a fault worth fixing urgently.
- *The bar for this point is set too high or too low* — changes the rubric.
- *This needs knowledge the system does not have* — a limit, written down rather
  than papered over.

### Your decision

**Approve** — the package is ready to send. Sending is a separate step,
done from the queue or by whoever runs the system, so an approval can be
looked at once more before anything leaves.
**Ask for more information** — the requests go to the candidate; the candidate
stays open.
**Do not proceed** — nothing is sent.

The trust slider is not a formality. It is one of the two numbers used to work
out whether this system is worth keeping.

### Roles

What each role asks for: every requirement with its question, its kind, its
weight, the number of quotations it needs and its examples; the coverage gate;
the bands. Edit any of it, add or remove a requirement, duplicate a role as the
start of a new one, or reset to the shipped version.

Nothing saves until the whole role validates, and the change is in force on the
next run. Candidates already reviewed keep the version they were scored under;
the page shows the hash of the version in use.

Two settings people get wrong:

**Quotations needed** — how many separate quotations a point needs before it
counts as met. Raise it to make the system harder to convince.

**Coverage gate** — how much of the role has to be assessable before a score is
reported at all. Below it, the system says it does not know enough rather than
guessing low. Seventy percent is strict for one-page CVs; that is a choice.

### Settings

The model provider and its key (shown as set or not, never the value), the
model and price per tier, the reviewer id that decisions are attributed to,
blind mode, and retention. **Test the connection** makes one tiny call per
tier and says what it cost.

## If something looks wrong

A quotation that is not in the CV, a question about something the CV clearly
answered, a mention of anything on the forbidden list, a recommendation whose
reasoning does not follow: these are all worth reporting. Correct the assessment
first — that records what happened in a form somebody can act on.
