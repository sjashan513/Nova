"""
Tests for core/domain/exceptions.py.

These formalize the checks that were run by hand, as throwaway scripts,
during the T1 build session -- moved here so they run with a single
`pytest` invocation instead of living only in chat history.
"""

import pytest

from core.domain.exceptions import (
    NovaError,
    PlannerError,
    PlannerResponseError,
    PlannerValidationError,
    PlanContractError,
    WorkerNotFoundError,
    ToolNotFoundError,
    PlanContractErrorGroup,
    ExecutionError,
    StepExecutionError,
    RetriesExhaustedError,
    DivergenceError,
)


class TestHierarchyShape:
    """
    PlannerError and PlanContractError must be SIBLINGS under NovaError,
    not parent/child of each other. See NOVA_PLANNER_LAYER_ADR.md §2.2
    for why: they are sequential pipeline stages (PlannerError happens
    before a Plan object exists; PlanContractError presupposes one
    already does), not a specialization relationship.
    """

    def test_planner_error_is_a_nova_error(self):
        assert issubclass(PlannerError, NovaError)

    def test_plan_contract_error_is_a_nova_error(self):
        assert issubclass(PlanContractError, NovaError)

    def test_planner_error_is_not_a_plan_contract_error(self):
        assert not issubclass(PlannerError, PlanContractError)

    def test_plan_contract_error_is_not_a_planner_error(self):
        assert not issubclass(PlanContractError, PlannerError)


class TestSubtypeFamilies:
    """Each concrete exception must fall under its correct family."""

    @pytest.mark.parametrize(
        "subtype", [PlannerResponseError, PlannerValidationError]
    )
    def test_planner_error_children(self, subtype):
        assert issubclass(subtype, PlannerError)

    @pytest.mark.parametrize(
        "subtype",
        [WorkerNotFoundError, ToolNotFoundError, PlanContractErrorGroup],
    )
    def test_plan_contract_error_children(self, subtype):
        assert issubclass(subtype, PlanContractError)


class TestCatchByFamily:
    """
    A consumer should be able to do `except PlannerError` or
    `except PlanContractError` without enumerating every subtype, while
    still seeing the exact concrete type that fired (e.g. for logging).
    """

    def test_except_planner_error_catches_response_error(self):
        with pytest.raises(PlannerError) as exc_info:
            raise PlannerResponseError("bad json", raw_response="{not json")
        assert isinstance(exc_info.value, PlannerResponseError)

    def test_except_planner_error_catches_validation_error(self):
        original = ValueError("missing field")
        with pytest.raises(PlannerError) as exc_info:
            raise PlannerValidationError(
                "plan shape invalid", raw_response="{}", original_error=original
            )
        assert isinstance(exc_info.value, PlannerValidationError)
        assert exc_info.value.original_error is original

    def test_except_plan_contract_error_catches_group(self):
        err = WorkerNotFoundError(
            plan_id="p1",
            step_id="s2",
            raw_value="worker_ts_fxi",
            available_workers=["worker_ts_fix", "worker_jsdoc"],
        )
        group = PlanContractErrorGroup(plan_id="p1", errors=[err])

        with pytest.raises(PlanContractError) as exc_info:
            raise group
        assert isinstance(exc_info.value, PlanContractErrorGroup)


class TestPlanContractErrorGroup:
    """
    The Group must retain every error it was given -- the whole point
    of this class is that the Iniciador's retry loop never silently
    drops an unresolved error when it gives up.
    """

    def test_group_retains_all_errors_in_order(self):
        err1 = WorkerNotFoundError(
            plan_id="p1",
            step_id="s2",
            raw_value="worker_ts_fxi",
            available_workers=["worker_ts_fix"],
        )
        err2 = ToolNotFoundError(
            plan_id="p1",
            step_id="s5",
            raw_value="filesystemm",
            available_tools=["filesystem"],
        )

        group = PlanContractErrorGroup(plan_id="p1", errors=[err1, err2])

        assert len(group.errors) == 2
        assert group.errors[0] is err1
        assert group.errors[1] is err2
        assert group.errors[0].step_id == "s2"
        assert group.errors[1].step_id == "s5"


class TestExecutionErrorPopulatedInFase2:
    """
    ExecutionError stopped being reserved-and-empty in Fase 2
    (error_policy.py needed it for real Level-1-retry / Level-3-escalate
    behavior). This replaces the old "must stay empty" guard with a
    "must have exactly these subtypes, correctly shaped" guard -- the
    same spirit (catch unannounced changes) applied to its new state.
    """

    def test_execution_error_is_a_nova_error(self):
        assert issubclass(ExecutionError, NovaError)

    def test_step_execution_error_is_an_execution_error(self):
        assert issubclass(StepExecutionError, ExecutionError)

    def test_retries_exhausted_error_is_an_execution_error(self):
        assert issubclass(RetriesExhaustedError, ExecutionError)

    def test_step_execution_error_wraps_original_exception(self):
        original = OSError("file locked")
        err = StepExecutionError(
            step_id="s1", attempt=2, original_error=original)
        assert err.original_error is original
        assert err.attempt == 2
        assert err.step_id == "s1"

    def test_retries_exhausted_error_retains_all_attempts(self):
        attempts = [
            StepExecutionError(step_id="s1", attempt=1,
                               original_error=OSError("a")),
            StepExecutionError(step_id="s1", attempt=2,
                               original_error=OSError("b")),
        ]
        err = RetriesExhaustedError(step_id="s1", attempts=attempts)
        assert len(err.attempts) == 2
        assert err.attempts[0] is attempts[0]
        assert err.attempts[1] is attempts[1]


class TestDivergenceErrorStillReserved:
    """
    DivergenceError remains reserved for Fase 5. This session must not
    have populated it -- if it did, that's an out-of-scope change that
    snuck in.
    """

    def test_divergence_error_is_reserved_and_empty(self):
        assert issubclass(DivergenceError, NovaError)
        assert DivergenceError.__doc__ is not None
        assert "RESERVED" in DivergenceError.__doc__
