"""
Context resolution -- turns "$step_id.field" placeholders in a Step's
input into concrete values, using the accumulated results of already
-executed steps.

This is what keeps Workers completely stateless: a Worker never sees a
reference, never knows which step came before it, never reaches out to
fetch anything itself. The Director resolves every reference before
dispatch and hands the Worker a plain dict of real values. See
nova_technical_overview.md, "Context resolution":

    "Workers are completely stateless. They never know what step came
    before them. The Director resolves all $step_id.field references
    before dispatching each step, injecting concrete values from the
    accumulated plan context."

Reference syntax (sealed for v1, see Fase 2 design session): a value is
treated as a reference ONLY if it, in its entirety, matches
"$<step_id>.<field>" exactly -- partial interpolation inside a longer
string (e.g. "result was $s1.content") is NOT supported. This was a
deliberate scope limit, not an oversight: partial interpolation opens
ambiguous parsing questions (where does the reference end?) that
nothing in the ADR asked for. Extending to partial interpolation later
is an additive change, not a breaking one.

List support (added after T8's closure test surfaced a real need):
values inside a top-level LIST are also checked individually -- e.g.
{"args": ["$s1.path"]} resolves the single list element. This exists
because tools/terminal.py's revised contract (command + args, see the
Fase 2 design session note on the whitelist-vs-reference-syntax
incompatibility found in T8) needs a dynamic, resolved value inside a
list of arguments. Still does NOT recurse into nested dicts, and does
NOT support a list element that is a dict or another list -- only
flat lists of strings/literals, since that is the only real need found
so far. Extending further is additive, not breaking, same principle as
the top-level scope limit above.
"""

import re
from typing import Any, Dict, List

from core.domain.models import Step

# Matches a value that IS, in its entirety, a reference -- not a
# reference embedded inside other text. Group 1 is the step_id, group 2
# is the field name within that step's result dict.
_REFERENCE_PATTERN = re.compile(r"^\$([^.\s]+)\.([^.\s]+)$")


def _resolve_value(
    value: Any,
    context: Dict[str, Dict[str, Any]],
    step_id: str,
    key: str,
) -> Any:
    """
    Resolves a single value: if it's a string matching the full
    reference pattern, returns the real value from context. Otherwise
    returns the value unchanged. `step_id` and `key` are only used to
    build clear error messages -- they don't affect resolution logic.
    """
    if not isinstance(value, str):
        return value

    match = _REFERENCE_PATTERN.match(value)
    if not match:
        return value

    ref_step_id, field = match.group(1), match.group(2)

    if ref_step_id not in context:
        raise KeyError(
            f"Step '{step_id}' input '{key}' references '{value}', but "
            f"step '{ref_step_id}' is not yet in context (not executed, "
            f"or produced no result)."
        )

    ref_result = context[ref_step_id]
    if field not in ref_result:
        raise KeyError(
            f"Step '{step_id}' input '{key}' references '{value}', but "
            f"step '{ref_step_id}'s result has no field '{field}'. "
            f"Available fields: {list(ref_result.keys())}"
        )

    return ref_result[field]


def resolve_step_input(step: Step, context: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Returns a new dict, the same shape as step.input, with every
    "$step_id.field" reference replaced by the real value found at
    context[step_id][field]. Checks top-level values directly, and
    checks each element of a top-level LIST value individually (see
    module docstring) -- does not recurse into nested dicts or lists
    beyond that one level, since no real use case has needed it yet.

    `context` accumulates as the Director executes a plan: after a
    step completes, its `result` dict gets stored under context[step.id].
    This function only ever READS from context -- it never mutates it
    and never executes anything; the Director is responsible for
    populating context as steps complete, before calling this for the
    next step that needs it.

    Raises:
        KeyError: a reference names a step_id that is not in `context`
            (the Director never dispatches a step before its
            dependencies have completed and written to context, so
            this should only fire if context was populated incorrectly
            -- it is a defensive check, not an expected runtime path),
            or names a step_id that IS in context but has no matching
            field in its result.
    """
    resolved: Dict[str, Any] = {}

    for key, value in step.input.items():
        if isinstance(value, list):
            resolved[key] = [
                _resolve_value(item, context, step.id, key) for item in value
            ]
        else:
            resolved[key] = _resolve_value(value, context, step.id, key)

    return resolved
