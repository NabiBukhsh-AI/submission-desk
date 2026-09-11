# Using Submission Desk

For the person doing the screening. No technical background assumed.

## What this does

It reads a candidate's CV against a list of things the role needs, and shows you
what the documents actually say about each one — quoted, with the page number.

It does not decide anything. It produces a recommendation and the reasoning
behind it, and you decide. Nothing is sent to a candidate until you approve it.

## What it will not do

It will not tell you someone is a good fit. It will tell you what their
documents say about nine specific points, and what a fixed set of rules makes of
that.

It will not guess. If a CV never mentions evaluation work, the system says the
CV never mentioned it — not that the candidate cannot do it, and not that they
scored badly on it.

It will never mention age, gender, nationality, ethnicity, religion, marital or
family status, disability, personality, culture fit, or appearance. If you see
any of those anywhere in this system, that is a bug worth reporting immediately.

## Getting started

    make demo

Then open the address it prints.

## The four pages

### Upload

Drag in CVs and supporting documents. The system guesses which files belong to
the same person and shows you the grouping so you can correct it — filenames are
how people name things, not how systems identify them, and it will sometimes get
this wrong.

Limits are shown before you upload rather than after: 20 MB per file, six
documents per candidate.

### Queue

Everything that has been processed. It opens on **Needs attention**, which is
the candidates waiting on you. The other filters are there when you want them.

The chips mean:

| Chip | What it means |
|---|---|
| ✅ Ready to review | Everything checked out. Open it when you have a moment. |
| ⚠️ Needs a closer look | Something wants your judgement. The page says what. |
| 🛑 Documents flagged | A document contained content aimed at an automated reader. Nothing was assessed and nothing was spent. |
| ✉️ Waiting on the candidate | You asked for more information. Nothing to do until it arrives. |
| ⚠️ Could not finish | A step failed. The documents are still there to read. |
| 👍 / 👎 | You have decided. |

### Review

The page is ordered by the questions you will actually ask.

**Can I trust these documents?** The banner at the top. Green means nothing was
found. Amber means something was flagged and the assessment continued anyway.
Red means a document contained content designed to manipulate an automated
reader — the flagged text is quoted in full so you can see exactly what it was.

**What does it recommend, and why?** The band, then every rule that fired, in
plain sentences. If it says "not enough information", that is not a low score.
It means the documents did not cover enough of the role to judge, and the
questions further down say what to ask for.

**What did it read?** Each point, expandable, with the quotations behind it.
"Show me where" puts the quotation back in its surrounding paragraph so you can
see whether it was quoted fairly.

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

### Rubric

What the role asks for: the points, their weights, how many quotations each one
needs, and where the bands fall.

Editing this changes how every candidate is scored from now on. Nothing saves
until it validates, and you see exactly what changes before it is written.
Candidates already reviewed keep the rubric they were scored under.

Two fields people get wrong:

**`min_supported`** — how many separate quotations a point needs before it
counts as met. Raise it to make the system harder to convince.

**`min_coverage`** — how much of the rubric has to be assessable before a score
is reported at all. Below it, the system says it does not know enough rather
than guessing low.

## If something looks wrong

A quotation that is not in the CV, a question about something the CV clearly
answered, a mention of anything on the forbidden list, a recommendation whose
reasoning does not follow: these are all worth reporting. Correct the assessment
first — that records what happened in a form somebody can act on.
