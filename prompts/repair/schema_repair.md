---
id: repair/schema_repair
version: 1
purpose: >-
  Ask a model to correct its own malformed response, showing the validation
  error and not the document again.
inputs: [schema_name, previous_output, validation_error]
output_schema: none
invariants:
  - "Never resends the candidate document; the model has already read it"
  - "Quotes the exact validation error rather than paraphrasing it"
  - "Used at most once per call, at the same tier"
forbidden:
  - "Inviting the model to invent content to satisfy a required field"
  - "Being retried in a loop"
---

Your previous response did not match the required schema.

Schema: {schema_name}

What you returned:
{previous_output}

What was wrong with it:
{validation_error}

Return only corrected JSON matching the schema.

Do not explain the correction. Do not add fields that are not in the schema. If
a required field cannot be supported by what you were shown, use the schema's
way of recording absence rather than inventing a value: a fabricated field is
worse than a missing one, and will be rejected downstream anyway.
