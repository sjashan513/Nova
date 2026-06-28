"""
DirectorInstance -- one instance per active Plan, per
NOVA_DIRECTOR_LAYER_ADR.md §4.3: "Una instancia, un Plan. El estado de
un Director no se comparte ni se reutiliza entre tareas."

This is a class, not a module of free functions, specifically because
multiple Directors will exist concurrently once the Router (Fase 8)
is built. Each instance owns its own `context` dict -- nothing here is
module-level mutable state, so two DirectorInstances running at once
(a Fase 8 concern, not exercised yet in Fase 2) never share memory by
accident.

Fase 2 scope: a single instance, single Plan, primitive Steps only
(filesystem/terminal, no LLM workers yet). No Router, no locks, no
worktree/container isolation -- those are Fase 8. This class assumes
it is the only Director running.

Fase 3 addition: real Worker steps. Two things changed here, and only
here -- no other module needed to change for this:

  1. Calling convention split (see NOVA_WORKER_LAYER_ADR.md / session
     notes): primitive tools (filesystem.read, terminal.run) take
     named kwargs, so they're still called as `dispatch_fn(**input)`.
     A Worker's BaseWorker.run() takes ONE dict parameter (input: Dict),
     deliberately not **kwargs (a dynamic kwargs signature would
     silently swallow or confusingly fail on an unexpected key instead
     of failing at the one obvious call site) -- so Worker steps are
     called as `dispatch_fn(input)` instead. _execute_step picks the
     right convention using the same "worker_" naming check
     validators.py already uses.
  2. Worker steps with a model now route through
     core/director/error_policy.py::execute_with_fallback instead of
     calling execute_with_retry directly -- this is Level 2 (model
     fallback). The model is injected into the resolved input dict
     right before dispatch; the Worker itself never knows two models
     exist, it just reads "model" from its own input like any other
     field (session decision: the Director owns re-invoking with a
     different model, not the Worker).

Pipeline, per run():

    1. validate_dag(plan)              -> sorted_steps, dag errors
    2. validate_input_references(plan) -> input-reference errors
       (both error sets raise PlanContractErrorGroup if non-empty --
       this is contract validation, same category as Fase 0/1's
       checks, just for the dependency graph instead of the registry)
    3. build_levels(sorted_steps)       -> List[List[Step]]
    4. for each level, in order:
         for each step in the level, IN PARALLEL (ThreadPoolExecutor):
           a. resolve_step_input(step, self.context)
           b. dispatch to the real tool/worker function
           c. execute_with_retry (Level 1) or execute_with_fallback
              (Level 1 + Level 2, Worker steps with a model)
           d. on success: store result in self.context[step.id]
           e. on RetriesExhaustedError: Level 3 (Fase 2 minimal form)
              -- mark the Plan FAILED, stop, propagate the error
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Literal, Optional

from core.domain.models import Plan, Step
from core.domain.exceptions import (
    PlanContractErrorGroup,
    RetriesExhaustedError,
)
from core.director.dag import validate_dag, validate_input_references, build_levels
from core.director.context import resolve_step_input
from core.director.error_policy import execute_with_retry, execute_with_fallback
from tools import filesystem, terminal
from workers.coding.worker_ts_check import WorkerTsCheck
from workers.coding.worker_ts_fix import WorkerTsFix
from workers.coding.worker_jsdoc import WorkerJsdoc
from workers.coding.worker_test_writer import WorkerTestWriter
from workers.coding.worker_commit_msg import WorkerCommitMsg
from workers.coding.worker_diff_summary import WorkerDiffSummary

PlanStatus = Literal["PENDING", "RUNNING", "DONE", "FAILED"]

# Same "worker_" naming convention already used in
# core/planner/validators.py to distinguish workers from primitive
# tools. Duplicated here rather than imported -- this is a tiny
# string-prefix convention, not a shared piece of logic; importing it
# would couple director/ to planner/ for one constant, which the
# dependency direction in NOVA_CLI_MVP_ROADMAP.md §0.2 doesn't call
# for.
_WORKER_PREFIX = "worker_"

# Maps (tool_or_worker, action) to the real callable that performs it.
# tool_or_worker alone is not enough to dispatch -- the registry only
# ever registers "filesystem" or "terminal" as tool names (see
# registry/tool_registry.yaml), never "filesystem.read" -- so `action`
# (Step.action) is what distinguishes filesystem.read from
# filesystem.write from filesystem.list. Worker entries use "" as their
# action (a worker_ts_fix step IS the action -- see Step.action's
# docstring) -- e.g. ("worker_ts_fix", "") maps to that worker's
# BaseWorker.run.
#
# Fase 3: the 6 coding workers built this session are registered here
# AND in core/planner/planner_prompt.py's IMPLEMENTED_TOOLS_AND_WORKERS
# -- same commit, per NOVA_PENDIENTE_POST_FASE2.md §3.5: these two
# lists must never drift out of sync, since a mismatch produces a
# confusing runtime error instead of Kimi simply not proposing
# something that has no real dispatch behind it yet.
#
# Each entry is `.run` (BaseWorker's concrete wrapper), never
# `.execute` directly -- `.run` is what enforces the WorkerOutput
# contract and performs the status -> exception translation
# (workers/base.py). Dispatching to `.execute` would skip that
# entirely and leak a raw WorkerOutput dict (or worse, an unhandled
# status: "error" case) straight into _execute_step.
_TOOL_DISPATCH: Dict[tuple, Callable[..., Dict[str, Any]]] = {
    ("filesystem", "read"): filesystem.read,
    ("filesystem", "write"): filesystem.write,
    ("filesystem", "list"): filesystem.list_dir,
    ("terminal", "run"): terminal.run,
    ("worker_ts_check", ""): WorkerTsCheck().run,
    ("worker_ts_fix", ""): WorkerTsFix().run,
    ("worker_jsdoc", ""): WorkerJsdoc().run,
    ("worker_test_writer", ""): WorkerTestWriter().run,
    ("worker_commit_msg", ""): WorkerCommitMsg().run,
    ("worker_diff_summary", ""): WorkerDiffSummary().run,
}


class DirectorInstance:
    """
    Orchestrates execution of exactly one Plan. Create a new instance
    per Plan -- do not reuse an instance across tasks (see module
    docstring; this mirrors the ADR's explicit "one instance, one
    Plan" rule).
    """

    def __init__(self, plan: Plan, plan_id: Optional[str] = None):
        self.plan = plan
        self.plan_id = plan_id
        self.context: Dict[str, Dict[str, Any]] = {}
        self.status: PlanStatus = "PENDING"

    def run(self) -> Dict[str, Any]:
        """
        Validates the plan's dependency graph, builds execution levels,
        and executes every step, level by level, with same-level steps
        running in parallel.

        Returns a summary dict: {"status": "DONE", "context": {...}}
        on success.

        Raises:
            PlanContractErrorGroup: the plan's dependency graph or
                input references are invalid. Never starts executing
                anything in this case -- same guarantee as the
                Iniciador's contract checks (Fase 0/1): a structurally
                invalid plan never reaches execution.
            RetriesExhaustedError: a step exhausted its Level-1 (and,
                for Worker steps with a model, Level-2) retry budget.
                self.status is set to "FAILED" before this propagates
                -- Fase 2's minimal Level 3: no Gemma, no Ventanilla to
                notify, so the caller (cli.py) is responsible for
                presenting this to Jashan.
        """
        self.status = "RUNNING"

        sorted_steps, dag_errors = validate_dag(
            self.plan, plan_id=self.plan_id)
        if dag_errors:
            self.status = "FAILED"
            raise PlanContractErrorGroup(
                plan_id=self.plan_id, errors=dag_errors)

        input_errors = validate_input_references(
            self.plan, plan_id=self.plan_id)
        if input_errors:
            self.status = "FAILED"
            raise PlanContractErrorGroup(
                plan_id=self.plan_id, errors=input_errors)

        levels = build_levels(sorted_steps)

        for level in levels:
            self._execute_level(level)

        self.status = "DONE"
        return {"status": "DONE", "context": self.context}

    def _execute_level(self, level: List[Step]) -> None:
        """
        Executes every step in `level` in parallel via a thread pool --
        see Fase 2 design session for why threads (I/O-bound tools,
        not CPU-bound) over asyncio. Steps in the same level have no
        dependency relationship to each other (that's what a level
        IS, per build_levels), so running them concurrently is always
        safe -- no step in this list can need another step in this
        same list's result.

        If any step in the level exhausts its retries, the first such
        RetriesExhaustedError encountered is re-raised after all
        futures in this level complete (or fail) -- siblings in the
        same level are not cancelled mid-flight, since they have no
        dependency on the failing step and their results may still be
        useful context for whatever happens next (e.g. Jashan choosing
        "skip" at Level 3 in a future phase).
        """
        with ThreadPoolExecutor(max_workers=len(level)) as executor:
            futures = {
                executor.submit(self._execute_step, step): step
                for step in level
            }

            first_error: Optional[RetriesExhaustedError] = None
            for future, step in futures.items():
                try:
                    result = future.result()
                    self.context[step.id] = result
                except RetriesExhaustedError as e:
                    if first_error is None:
                        first_error = e

            if first_error is not None:
                self.status = "FAILED"
                raise first_error

    def _execute_step(self, step: Step) -> Dict[str, Any]:
        """
        Resolves this step's input references against the current
        context, dispatches to the real tool/worker function, and runs
        it through the appropriate error policy.

        Note: self.context is only READ here (via resolve_step_input),
        never written -- writes happen in _execute_level after this
        returns, from the main thread, to avoid two steps in the same
        level racing to write to the shared dict (reads are safe
        concurrently; this function never mutates self.context itself).

        Three distinct dispatch shapes, decided here (Fase 3 addition
        for the last two; the first is unchanged from Fase 2):

          1. Primitive tool (not "worker_"-prefixed): unchanged from
             Fase 2. `dispatch_fn(**resolved_input)`, single
             execute_with_retry call -- no model, no fallback.

          2. Worker step with step.model is None (a worker registered
             with requires_model: false, e.g. worker_ts_check):
             `dispatch_fn(resolved_input)` -- ONE dict argument, per
             BaseWorker.run()'s signature -- single execute_with_retry
             call, no fallback routing (nothing to fall back to).

          3. Worker step with step.model set: builds a small
             model-parameterized factory and routes through
             execute_with_fallback (Level 1 + Level 2). The factory
             injects "model" into a COPY of resolved_input each time
             it's called -- once with step.model, and again with
             step.fallback_model only if the first attempt's retry
             budget is fully exhausted. The Worker itself never knows
             two models exist; it just reads "model" from its own
             input like any other field.
        """
        resolved_input = resolve_step_input(step, self.context)

        dispatch_key = (step.tool_or_worker, step.action)
        dispatch_fn = _TOOL_DISPATCH.get(dispatch_key)
        if dispatch_fn is None:
            raise ValueError(
                f"No dispatch registered for tool_or_worker="
                f"'{step.tool_or_worker}', action='{step.action}' -- this "
                f"should have been caught by contract validation before "
                f"execution; if it wasn't, the registry and this dispatch "
                f"table have drifted out of sync."
            )

        is_worker_step = step.tool_or_worker.startswith(_WORKER_PREFIX)

        if not is_worker_step:
            return execute_with_retry(
                fn=lambda: dispatch_fn(**resolved_input),
                step_id=step.id,
            )

        if step.model is None:
            return execute_with_retry(
                fn=lambda: dispatch_fn(resolved_input),
                step_id=step.id,
            )

        def make_call(model: Optional[str]) -> Callable[[], Dict[str, Any]]:
            # A fresh dict per call -- never mutate resolved_input
            # itself, since make_call may be invoked twice (primary,
            # then fallback) and each call must carry its own model
            # without the second overwriting context the first one
            # still needs if something inspects it after the fact.
            step_input = {**resolved_input, "model": model}
            return lambda: dispatch_fn(step_input)

        return execute_with_fallback(
            fn_factory=make_call,
            step_id=step.id,
            primary_model=step.model,
            fallback_model=step.fallback_model,
        )
