"""The one function per provider that performs the HTTP call.

Everything above this package speaks in tiers and schemas. A transport takes
the payload ``ProviderModelClient`` assembles and returns
``{"id", "text", "usage": {"input_tokens", "output_tokens", "cached_input_tokens"}}``
with usage read from the provider's response and never estimated. It raises
``ModelUnavailable`` with a sentence a recruiter can act on for anything the
run cannot proceed past.
"""

from __future__ import annotations

from typing import Any

#: Schema keywords that describe shape. Everything else — lengths, ranges,
#: patterns, formats — is validation, and validation happens against the
#: pydantic model after the response arrives, so a provider that does not
#: understand a constraint keyword is never sent one.
SHAPE_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "const",
        "anyOf",
        "oneOf",
        "allOf",
        "$ref",
        "$defs",
        "definitions",
        "additionalProperties",
        "description",
        "title",
        "default",
    }
)


#: Keywords whose values are maps of arbitrary names to schemas. The names
#: are the contract's field names, not keywords, and pass through untouched.
NAMED_MAPS = frozenset({"properties", "$defs", "definitions"})


def portable_schema(schema: Any) -> Any:
    """A JSON schema reduced to shape, for providers with a schema subset."""
    if isinstance(schema, dict):
        return {
            key: (
                {name: portable_schema(sub) for name, sub in value.items()}
                if key in NAMED_MAPS and isinstance(value, dict)
                else portable_schema(value)
            )
            for key, value in schema.items()
            if key in SHAPE_KEYWORDS
        }
    if isinstance(schema, list):
        return [portable_schema(item) for item in schema]
    return schema
