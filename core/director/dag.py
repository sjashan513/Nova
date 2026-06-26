"""
DAG validation and execution-level construction for the Director.

Two functions, called in sequence:

    sorted_steps, errors = validate_dag(plan)
    if not errors:
        levels = build_levels(sorted_steps)

validate_dag does NOT assume the plan's steps array is topologically
ordered -- it checks that every depends_on reference points to a real
step_id in the same plan, and that the graph has no cycles. While doing
the cycle check, it necessarily computes a valid topological order as
a side effect (you cannot detect "no steps are placeable" without
figuring out, round by round, which steps ARE placeable). That order
is returned alongside the error list instead of being thrown away --
build_levels then runs as a single O(n) pass over already-sorted
steps, exactly the original single-pass design (a step_id -> level
lookup, no re-scanning), instead of repeating the same O(n^2) sorting
work a second time.

This is the fix for an earlier version of this module that computed a
valid order twice -- once inside validate_dag purely to detect cycles,
then discarded it, and again inside build_levels from scratch. Same
total information, computed once.
"""

from typing import Dict, List, Optional, Tuple
import re

from core.domain.models import Plan, Step
from core.domain.exceptions import (
    PlanContractError,
    InvalidStepReferenceError,
    CyclicDependencyError,
    UndeclaredDependencyError,
)

# Mirrors core/director/context.py's _REFERENCE_PATTERN exactly -- this
# module checks the SAME syntax that context.py resolves, just earlier
# in the pipeline (contract validation, before any execution), and
# against a different rule (declared in depends_on, not "does the
# referenced step exist at all").
_REFERENCE_PATTERN = re.compile(r"^\$([^.\s]+)\.([^.\s]+)$")


def validate_dag(
    plan: Plan, plan_id: Optional[str] = None
) -> Tuple[List[Step], List[PlanContractError]]:
    """
    Checks the plan's dependency graph for two distinct problems:

      1. Dangling references -- a step's depends_on names a step_id
         that does not exist anywhere in this plan.
      2. Cycles -- the graph has no valid topological order at all.

    Does NOT raise. Returns (sorted_steps, errors):
      - errors is a list (empty = valid), same pattern as
        validate_plan_against_registry (T2, Fase 1) -- the caller
        gets the complete picture in one pass, not just the first
        problem found.
      - sorted_steps is the plan's steps in a valid topological order
        -- every step's dependencies appear earlier in the list than
        the step itself. This is ONLY meaningful when errors is empty;
        it is returned as [] when errors is non-empty, since no valid
        order exists (or dangling references make the question
        meaningless).

    Cycle detection strategy: repeatedly find steps whose dependencies
    are all already placed, place them, repeat. If a round places
    nothing while steps remain, the remainder must form a cycle --
    there is no other way a valid DAG could get stuck, since dangling
    references were already ruled out by the time this check runs.
    This walk doubles as the topological sort: the order steps get
    placed in, round by round, IS a valid topological order.
    """
    errors: List[PlanContractError] = []

    known_ids = {step.id for step in plan.steps}

    for step in plan.steps:
        for dep_id in step.depends_on:
            if dep_id not in known_ids:
                errors.append(
                    InvalidStepReferenceError(
                        plan_id=plan_id,
                        step_id=step.id,
                        raw_value=dep_id,
                        known_step_ids=sorted(known_ids),
                    )
                )

    if errors:
        # Dangling references make cycle detection (and therefore a
        # topological order) meaningless -- report these first, let
        # the caller fix them before we attempt to reason about cycles.
        return [], errors

    placed_ids: set = set()
    sorted_steps: List[Step] = []
    remaining: List[Step] = list(plan.steps)

    while remaining:
        placeable = [
            step for step in remaining
            if all(dep_id in placed_ids for dep_id in step.depends_on)
        ]

        if not placeable:
            # Every remaining step has at least one dependency that is
            # itself remaining -- only possible with a cycle, since
            # dangling references were already ruled out above.
            cyclic_ids = [step.id for step in remaining]
            errors.append(
                CyclicDependencyError(
                    plan_id=plan_id, cyclic_step_ids=cyclic_ids)
            )
            return [], errors

        for step in placeable:
            placed_ids.add(step.id)
            sorted_steps.append(step)

        remaining = [step for step in remaining if step.id not in placed_ids]

    return sorted_steps, errors


def validate_input_references(
    plan: Plan, plan_id: Optional[str] = None
) -> List[PlanContractError]:
    """
    Checks that every "$step_id.field" reference in each step's `input`
    points to a step_id that is listed in THAT SAME step's depends_on.

    This is independent of validate_dag's checks (dangling depends_on
    entries, cycles) -- a step can have perfectly valid depends_on and
    still reference, in its input, a DIFFERENT step it never declared
    a dependency on. validate_dag would not catch that; this does.

    Intended to run AFTER validate_dag returns no errors -- it assumes
    every step_id mentioned anywhere in the plan that validate_dag
    would have flagged as dangling has already been ruled out. If
    called on a plan that hasn't passed validate_dag, a reference to a
    truly nonexistent step_id will surface here too (it simply won't
    be in any step's depends_on, since it doesn't exist at all), but
    the error message will frame it as "not declared" rather than
    "doesn't exist" -- less precise, which is exactly why the Director
    should call validate_dag first.

    Returns a list of errors (empty = valid), same pattern as every
    other contract check in this codebase -- the caller gets every
    problem in one pass, not just the first.
    """
    errors: List[PlanContractError] = []

    for step in plan.steps:
        declared = set(step.depends_on)

        for value in step.input.values():
            if not isinstance(value, str):
                continue

            match = _REFERENCE_PATTERN.match(value)
            if not match:
                continue

            ref_step_id = match.group(1)
            if ref_step_id not in declared:
                errors.append(
                    UndeclaredDependencyError(
                        plan_id=plan_id,
                        step_id=step.id,
                        raw_value=ref_step_id,
                        declared_depends_on=sorted(declared),
                    )
                )

    return errors


def build_levels(sorted_steps: List[Step]) -> List[List[Step]]:
    """
    Groups steps into execution levels: level 0 contains every step
    with no dependencies, level 1 contains steps whose dependencies
    are all in level 0 (or earlier), and so on. Steps in the same
    level have no dependency relationship to each other and are safe
    to execute in parallel.

    REQUIRES `sorted_steps` to already be in a valid topological order
    -- i.e. the first element of the tuple returned by validate_dag,
    called first, with an empty error list. This function does not
    re-check for dangling references or cycles, and does not re-sort;
    it assumes both were already handled by validate_dag. Calling this
    with an arbitrarily-ordered list, or a list from a plan that
    failed validate_dag, has undefined behavior.

    Algorithm: single O(n) pass, using a step_id -> level lookup
    (`levels_by_id`) to compute each step's level in O(1) per
    dependency, instead of re-scanning remaining steps on every
    iteration. A step's level is 0 if it has no dependencies,
    otherwise it is one more than the MAXIMUM level of all its
    dependencies -- not just one dependency, since a step can depend
    on several steps that landed in different levels. This is safe to
    do in a single linear pass specifically because `sorted_steps` is
    already topologically ordered: every dependency of a step is
    guaranteed to already be in `levels_by_id` by the time that step
    is reached.
    """
    levels_by_id: Dict[str, int] = {}
    matrix: List[List[Step]] = []

    for step in sorted_steps:
        if not step.depends_on:
            level = 0
        else:
            level = max(levels_by_id[dep_id] for dep_id in step.depends_on) + 1

        levels_by_id[step.id] = level

        while len(matrix) <= level:
            matrix.append([])
        matrix[level].append(step)

    return matrix
