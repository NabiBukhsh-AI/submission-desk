---
id: composition/questions
version: 1
purpose: >-
  Phrase gaps a recruiter can ask about, and interview questions that probe the
  points the documents did not settle.
inputs: [gaps, profile_summary]
output_schema: CompositionResponse
invariants:
  - "Every question names a criterion that is genuinely unresolved"
  - "Which gaps exist was decided before this call; only the wording is asked for"
  - "Questions are answerable by the candidate from their own experience"
forbidden:
  - "Inventing a gap that is not in the list supplied"
  - "Asking about age, gender, nationality, ethnicity, religion, marital status, family status, disability, personality, culture fit, or appearance"
  - "Asking a question whose answer would be a self-assessment rather than an account of something done"
  - "Emitting a score, rating, or opinion about the candidate"
---

Below are points this candidate's documents did not settle. For each one, write
two things a recruiter can use.

An information request: one sentence asking the candidate to supply what is
missing. It goes into an email, so write it as something a person would send,
not as a form field.

An interview question: something to ask that would settle the point. Ask for an
account of something the candidate did, not for their opinion of themselves.
"Tell me about a time you..." settles a question; "How would you rate your..."
does not.

Do not add points. The list below was worked out from the evidence before you
were asked, and a gap you invent is a gap that does not exist.

Ask nothing about age, gender, nationality, ethnicity, religion, marital
status, family status, disability, personality, culture fit, or appearance.
These are not permitted subjects, including indirectly: no questions about
availability that are really about caring responsibilities, and none about
right to work that are really about origin.

The unresolved points:
{gaps}

For context, what the documents did establish:
{profile_summary}
