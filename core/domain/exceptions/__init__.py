"""
Domain exceptions — the small, closed vocabulary of things that can go
wrong in Nova.

This is a package, not a single module, as of Fase 3 (see
NOVA_WORKER_LAYER_ADR.md / session notes): the original single-file
core/domain/exceptions.py grew large enough across Fase 0-2 that
splitting it by family is worth doing, but the SHAPE of the hierarchy
itself is unchanged from when it was one file -- only the physical
layout changed. Every name below is re-exported here so existing code
that does `from core.domain.exceptions import PlannerError` (or any
other name) keeps working completely unchanged after this split --
nothing outside this package needs to know it's a package at all.

Design decision (sealed in the Fase 0 design session, June 2026; extended
in the Fase 1 / Planner Layer ADR, June 2026; extended again in the
Fase 2 / Director Execution Layer ADR and the Fase 3 / Worker Layer ADR):

    NovaError                       # root, never raised directly
      |
      +-- PlannerError              # everything that can fail between
      |     |                       # "Kimi responded" and "we have a
      |     |                       # valid Plan object" -- raised by
      |     |                       # core/planner/planner.py
      |     |
      |     +-- PlannerResponseError    # response is not parseable JSON,
      |     |                          # or is missing the top-level keys
      |     |                          # (status / plan)
      |     +-- PlannerValidationError  # top-level shape is fine, but the
      |                                 # "plan" payload does not instantiate
      |                                 # as Plan/Step -- wraps the native
      |                                 # pydantic.ValidationError, never lets
      |                                 # it escape unwrapped
      |
      +-- PlanContractError         # the Plan IS a valid object, but a
      |     |                       # Step references something that does
      |     |                       # not exist in the registry, or is
      |     |                       # otherwise contractually incomplete --
      |     |                       # caught BEFORE the plan ever reaches
      |     |                       # the Router/Director
      |     |
      |     +-- WorkerNotFoundError
      |     +-- ToolNotFoundError
      |     +-- MissingModelError          # Fase 3 -- worker step requires
      |     |                              # a model (registry: requires_
      |     |                              # model=true) but Step.model
      |     |                              # is not set
      |     +-- InvalidProjectError         # Fase 3 -- Step.input declares
      |     |                              # a "project" not in
      |     |                              # registry/project_registry.yaml
      |     +-- InvalidStepReferenceError  # depends_on references a
      |     |                              # step_id that doesn't exist
      |     |                              # in the plan (Fase 2)
      |     +-- CyclicDependencyError       # depends_on graph has a
      |     |                              # cycle, no valid execution
      |     |                              # order exists (Fase 2)
      |     +-- UndeclaredDependencyError   # input references a step_id
      |     |                              # not listed in depends_on
      |     |                              # (Fase 2)
      |     +-- PlanContractErrorGroup  # wraps multiple unresolved
      |                                 # PlanContractErrors after the
      |                                 # Iniciador's retry budget with
      |                                 # Kimi is exhausted
      |
      +-- ExecutionError            # Fase 2. Director/Worker runtime
      |     |                       # failures -- detected DURING
      |     |                       # execution, contrast with
      |     |                       # PlanContractError (detected
      |     |                       # BEFORE execution starts)
      |     |
      |     +-- StepExecutionError      # one failed attempt, wraps the
      |     |                           # real underlying exception
      |     +-- WorkerExecutionError    # Fase 3 -- a Worker's OWN
      |     |                           # internal error policy is
      |     |                           # already exhausted; sibling of
      |     |                           # RetriesExhaustedError (an
      |     |                           # outcome), not of
      |     |                           # StepExecutionError (one
      |     |                           # attempt)
      |     +-- RetriesExhaustedError   # Level 1 retry budget spent,
      |                                 # carries every attempt's error
      |
      +-- DivergenceError           # RESERVED — Fase 5. Empty on purpose.

Why PlannerError is a sibling of PlanContractError, not its parent or
child: these are sequential pipeline stages, not a specialization
relationship. PlannerError happens BEFORE a Plan object exists at all.
PlanContractError presupposes one already does.

Why one root with families instead of a flat list: a consumer
(Iniciador today, Director/Gemma later) can catch a family (`except
PlannerError`, `except PlanContractError`) without enumerating every
subtype, while the log and Jashan still see the exact subtype that
fired.

Ownership: PlannerError and PlanContractError (and all their children)
are raised and handled exclusively by the Iniciador (core/planner/).
ExecutionError (and its children) is owned by core/director/. The
Director never sees a plan that failed either of the Iniciador's
checks -- by design, a plan that fails parsing or contract validation
never reaches the Router.
"""

from core.domain.exceptions.nova_error import NovaError

from core.domain.exceptions.planner_errors import (
    PlannerError,
    PlannerResponseError,
    PlannerValidationError,
)

from core.domain.exceptions.contract_errors import (
    PlanContractError,
    WorkerNotFoundError,
    ToolNotFoundError,
    MissingModelError,
    InvalidProjectError,
    InvalidStepReferenceError,
    CyclicDependencyError,
    UndeclaredDependencyError,
    PlanContractErrorGroup,
)

from core.domain.exceptions.execution_errors import (
    ExecutionError,
    StepExecutionError,
    WorkerExecutionError,
    RetriesExhaustedError,
    PlanAbortedError
)

from core.domain.exceptions.divergence_errors import DivergenceError

__all__ = [
    "NovaError",
    "PlannerError",
    "PlannerResponseError",
    "PlannerValidationError",
    "PlanContractError",
    "WorkerNotFoundError",
    "ToolNotFoundError",
    "MissingModelError",
    "InvalidProjectError",
    "InvalidStepReferenceError",
    "CyclicDependencyError",
    "UndeclaredDependencyError",
    "PlanContractErrorGroup",
    "ExecutionError",
    "StepExecutionError",
    "WorkerExecutionError",
    "RetriesExhaustedError",
    "PlanAbortedError",
    "DivergenceError",
]
