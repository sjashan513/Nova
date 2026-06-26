"""
PlanContractError family — Fase 0, extended Fase 1/2/3. Owned by the
Iniciador.

A Plan produced by the Planner (Kimi) IS a valid object, but
references something that does not exist in the registry, or is
otherwise contractually incomplete (a broken dependency graph, a
worker step missing a required field). All of this is detected BEFORE
the plan ever reaches the Router/Director -- no lock is ever acquired
for a plan that fails any check in this family.
"""

from typing import List, Optional

from .nova_error import NovaError


class PlanContractError(NovaError):
    """
    A Plan produced by the Planner (Kimi) references a tool or worker
    that does not exist in the registry, or is otherwise contractually
    incomplete. This is a contract failure, not a runtime/execution
    failure — it is detected before the plan is handed to the Router,
    so no locks are ever acquired for it.
    """

    def __init__(
        self,
        message: str,
        plan_id: Optional[str],
        step_id: str,
        raw_value: str,
    ):
        self.plan_id = plan_id
        self.step_id = step_id
        self.raw_value = raw_value
        super().__init__(message)


class WorkerNotFoundError(PlanContractError):
    """
    Step.tool_or_worker looked like a worker (the "worker_" naming
    convention matched) but no worker with that exact name exists in
    the worker registry.
    """

    def __init__(
        self,
        plan_id: Optional[str],
        step_id: str,
        raw_value: str,
        available_workers: List[str],
    ):
        self.available_workers = available_workers
        message = (
            f"Step '{step_id}' references unknown worker '{raw_value}'. "
            f"Available workers: {', '.join(available_workers) or '(none registered)'}"
        )
        super().__init__(message, plan_id, step_id, raw_value)


class ToolNotFoundError(PlanContractError):
    """
    Step.tool_or_worker did not match the worker naming convention but
    no primitive tool with that exact name exists in the tool registry.
    """

    def __init__(
        self,
        plan_id: Optional[str],
        step_id: str,
        raw_value: str,
        available_tools: List[str],
    ):
        self.available_tools = available_tools
        message = (
            f"Step '{step_id}' references unknown tool '{raw_value}'. "
            f"Available tools: {', '.join(available_tools) or '(none registered)'}"
        )
        super().__init__(message, plan_id, step_id, raw_value)


class MissingModelError(PlanContractError):
    """
    Fase 3. A worker step references a worker that IS registered and
    DOES require a model (registry/tool_registry.yaml's
    requires_model: true for this worker), but Step.model is not set.

    Same category as WorkerNotFoundError/ToolNotFoundError -- detected
    before the Router, no lock acquired -- but a different problem:
    the worker name itself is valid, the Step is just incomplete for
    what it's trying to do. Only fires for workers with
    requires_model: true (see core/planner/validators.py::
    validate_worker_steps_have_model); a worker like worker_ts_check
    (no LLM) never triggers this, even with Step.model left unset,
    because there is nothing to set in the first place.

    Deliberately does NOT also require Step.fallback_model -- that
    field is optional by design (nova_technical_overview.md describes
    it as "if primary fails"). A Step with no fallback_model simply
    has no Level 2 model-fallback available and escalates straight to
    Level 3 if its primary model call fails -- expected behavior, not
    a contract violation.
    """

    def __init__(
        self,
        plan_id: Optional[str],
        step_id: str,
        raw_value: str,
    ):
        message = (
            f"Step '{step_id}' uses worker '{raw_value}', which requires "
            f"a model (registry: requires_model=true), but Step.model is "
            f"not set."
        )
        super().__init__(message, plan_id, step_id, raw_value)


class InvalidStepReferenceError(PlanContractError):
    """
    A Step's depends_on lists a step_id that does not exist anywhere
    else in the same plan. Same spirit as WorkerNotFoundError /
    ToolNotFoundError: the plan is not executable as given, detected
    before the Director ever attempts to build execution levels or
    acquire any lock.

    This was named (but not implemented) as a future example in
    NOVA_DOMAIN_LAYER_ADR.md §2.3 -- this is that future arriving in
    Fase 2, when core/director/dag.py needed it for real.
    """

    def __init__(
        self,
        plan_id: Optional[str],
        step_id: str,
        raw_value: str,
        known_step_ids: List[str],
    ):
        self.known_step_ids = known_step_ids
        message = (
            f"Step '{step_id}' depends on unknown step_id '{raw_value}'. "
            f"Known step ids in this plan: {', '.join(known_step_ids) or '(none)'}"
        )
        super().__init__(message, plan_id, step_id, raw_value)


class UndeclaredDependencyError(PlanContractError):
    """
    A Step's `input` contains a "$step_id.field" reference to a
    step_id that exists in the plan, but is NOT listed in this same
    step's own `depends_on`. Distinct from InvalidStepReferenceError,
    which is about depends_on pointing at something that doesn't exist
    at all -- this is about input and depends_on disagreeing with each
    other on a step that DOES exist.

    Why this matters operationally, not just stylistically: the
    Director only guarantees a step's result is in context once that
    step is a declared prerequisite (core/director/dag.py builds
    execution levels strictly from depends_on). A step that references
    another step's result without depending on it could be dispatched
    in the same level as -- or even before -- the step it's reading
    from, since the level-building algorithm never saw that dependency
    declared. The reference might resolve by coincidence in some
    executions and fail in others, depending on dispatch timing -- the
    kind of intermittent failure that's expensive to debug later.
    Caught here, before any execution, makes it a deterministic
    contract failure instead.
    """

    def __init__(
        self,
        plan_id: Optional[str],
        step_id: str,
        raw_value: str,
        declared_depends_on: List[str],
    ):
        self.declared_depends_on = declared_depends_on
        message = (
            f"Step '{step_id}' input references step '{raw_value}' via "
            f"\"${raw_value}.<field>\", but '{raw_value}' is not in "
            f"this step's own depends_on {declared_depends_on}. Add it to "
            f"depends_on, or the referenced step's result is not "
            f"guaranteed to exist when this step runs."
        )
        super().__init__(message, plan_id, step_id, raw_value)


class CyclicDependencyError(PlanContractError):
    """
    The plan's depends_on graph contains a cycle (directly or
    transitively) -- no valid execution order exists. Detected by
    core/director/dag.py's level-building pass: if a pass over the
    remaining steps finds none whose dependencies are all already
    placed, but steps remain unplaced, that is only possible if the
    graph has a cycle (assuming InvalidStepReferenceError already
    ruled out dangling references).
    """

    def __init__(
        self,
        plan_id: Optional[str],
        cyclic_step_ids: List[str],
    ):
        self.cyclic_step_ids = cyclic_step_ids
        message = (
            f"Plan contains a dependency cycle involving step(s): "
            f"{', '.join(cyclic_step_ids)}"
        )
        # No single step/raw_value applies to a cycle as a whole --
        # same placeholder pattern as PlanContractErrorGroup below.
        first_id = cyclic_step_ids[0] if cyclic_step_ids else "(none)"
        super().__init__(message, plan_id=plan_id, step_id=first_id, raw_value=first_id)


class PlanContractErrorGroup(PlanContractError):
    """
    Raised by the Iniciador's retry loop (get_plan()) when
    MAX_CONTRACT_RETRIES is exhausted and one or more PlanContractErrors
    remain unresolved. Wraps all of them -- callers must never lose
    errors by only propagating the first one and logging the rest.

    Note this does NOT collect PlannerError instances (PlannerResponseError /
    PlannerValidationError) -- those are a separate, earlier pipeline
    stage. See NOVA_PLANNER_LAYER_ADR.md §2.2 for why they are siblings,
    not nested.
    """

    def __init__(
        self,
        plan_id: Optional[str],
        errors: List[PlanContractError],
    ):
        self.errors = errors
        step_ids = ", ".join(e.step_id for e in errors) or "(none)"
        message = (
            f"Plan contract validation failed after exhausting retries. "
            f"{len(errors)} unresolved error(s) in step(s): {step_ids}"
        )
        # PlanContractErrorGroup itself doesn't map to one single
        # step/raw_value -- it IS the group. step_id/raw_value below are
        # placeholders so the parent __init__ contract is satisfied;
        # callers should inspect `.errors` for the real detail, not these.
        first = errors[0] if errors else None
        super().__init__(
            message,
            plan_id=plan_id,
            step_id=first.step_id if first else "(none)",
            raw_value=first.raw_value if first else "(none)",
        )
