"""
BaseWorker — Capa 2 of the Fase 3 Worker layer (see NOVA_WORKER_LAYER_ADR.md
§2.4). Materializes the contract already sealed in NOVA_PRIMITIVES_ADR.md
§3.2-3.4: a Worker is a dict-in, dict-or-exception-out unit, arbitrarily
complex internally, as long as it respects that boundary toward the
Director.

This module does NOT assume LLM usage. worker_ts_check (no LLM, a
subprocess wrapper around tsc) inherits from BaseWorker exactly like
worker_ts_fix (LLM-powered) does -- the abstract method is where the
real work happens, whatever shape that work takes.

Frontier rule (sealed in the same design session that produced this
file): the envelope below (WorkerOutput: status/result/reason) is the
INTERNAL contract between a Worker's own abstract method and
BaseWorker.run()'s wrapper. It never crosses into
core/director/director_instance.py. On success, run() returns ONLY the
inner `result` dict -- the same shape filesystem.read/terminal.run
already return today. On error, run() never returns at all: it raises
WorkerExecutionError. The Director keeps receiving exactly what it
already knows how to handle; core/director/context.py and
director_instance.py needed zero changes for this.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Literal, Optional, TypedDict

from core.domain.exceptions import WorkerExecutionError


class WorkerOutput(TypedDict):
    """
    The envelope every Worker's abstract method must return. One fixed
    shape, not two -- even when the underlying failure is a caught
    exception from inside the worker's own logic (e.g. a network error
    during its own internal retry loop), that exception is serialized
    to text and carried in `reason`, never exposed as a separate key.
    Same principle StepExecutionError already uses elsewhere in this
    codebase: wrap the real exception into a message, don't invent a
    second shape for it.

    result is only meaningful when status == "success".
    reason is only meaningful when status == "error".
    A worker should never set both, but this is not enforced by the
    type itself -- BaseWorker.run() only ever reads the field that
    matches `status`.
    """

    status: Literal["success", "error"]
    result: Optional[Dict[str, Any]]
    reason: Optional[str]


class BaseWorker(ABC):
    """
    Base class for every Worker (coding/, qa/, environment/, and any
    future domain). Subclasses implement `execute()` -- never override
    `run()`, which is the fixed wrapper that enforces the WorkerOutput
    contract and performs the status -> exception translation that
    keeps the Director's dispatch table ignorant of Workers entirely
    (it just calls a callable and gets a dict back, or an exception,
    exactly like it already does for filesystem/terminal).

    Why `run()` is concrete and `execute()` is abstract, not the other
    way around: every Worker needs the SAME translation at the
    boundary (this is the part that must never vary), while what
    happens to produce a WorkerOutput is exactly the part that's
    supposed to vary per Worker -- a one-call LLM fix and a multi-step
    research loop both produce the same WorkerOutput shape, by
    completely different internal means.
    """

    @abstractmethod
    def execute(self, input: Dict[str, Any]) -> WorkerOutput:
        """
        The Worker's actual work. Must return a WorkerOutput -- never
        raise for an expected/handled failure (that's what status:
        "error" + reason is for). Raising here is reserved for truly
        unexpected failures the Worker's own internal policy never
        anticipated -- those propagate up through run() unchanged,
        same as they always would have without this class existing.
        """
        ...

    def run(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fixed wrapper, not overridden by subclasses. Calls execute(),
        then enforces the WorkerOutput contract:

          status == "success" -> returns output["result"] alone. This
            is the ONLY thing that crosses into the Director -- a
            plain dict, indistinguishable from what filesystem.read
            already returns today. $step_id.field resolution in
            core/director/context.py keeps working unmodified.

          status == "error" -> raises WorkerExecutionError(reason=...).
            This signals "the Worker exhausted its own internal error
            policy and could not produce a result" -- see
            core/director/error_policy.py's dedicated except clause,
            which deliberately does NOT spend Level-1 retry budget on
            this case (retrying the exact same model on a problem the
            Worker itself already gave up on wastes real NIM calls
            without attacking a different cause). Level 2 (model
            fallback) still gets its own chance afterward, since a
            different model is a genuinely different condition, not
            "the same thing again."
        """
        output = self.execute(input)

        if output["status"] == "success":
            result = output["result"]
            # Narrows Optional[Dict] -> Dict for the type checker, and
            # catches a worker breaking its own contract at runtime:
            # WorkerOutput allows result to be None (the error branch
            # needs that), but a worker that says "success" must have
            # actually set it. A None here means the worker's own
            # execute() is inconsistent with itself, not that the
            # Director should ever see a None as if it were a normal
            # empty result.
            assert result is not None, (
                "Worker contract violation: status='success' but "
                "result is None -- the worker's own execute() broke "
                "its own contract."
            )
            return result

        reason = output["reason"]
        assert reason is not None, (
            "Worker contract violation: status='error' but reason is "
            "None -- the worker's own execute() broke its own contract."
        )
        raise WorkerExecutionError(reason=reason)
