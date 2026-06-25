"""
Tests for core/planner/iniciador.py.

Per NOVA_CLI_MVP_ROADMAP.md §0.3 ("each piece is tested with a fake of
the next one, never with the whole system"), these tests mock
core.planner.planner.call directly -- the Iniciador is tested in
isolation from how planner.py actually talks to NIM. A separate,
explicitly-marked integration test against the real API exists outside
this suite (see test_iniciador_live.py, not run by default).
"""

from unittest.mock import patch

import pytest

from core.domain.models import Plan, Step
from core.domain.exceptions import (
    PlanContractErrorGroup,
    PlannerError,
    PlannerResponseError,
)
from core.planner.iniciador import Iniciador


def _step(step_id: str, tool_or_worker: str) -> Step:
    return Step(id=step_id, description="test step", tool_or_worker=tool_or_worker)


def _ready(*steps: Step) -> dict:
    return {
        "status": "ready",
        "questions": [],
        "plan": Plan(objective="test objective", steps=list(steps)),
    }


def _clarification(questions: list) -> dict:
    return {"status": "clarification_needed", "questions": questions, "plan": None}


class TestHappyPath:
    def test_valid_plan_returns_immediately_without_retry(self):
        call_count = {"n": 0}

        def fake_call(task, retry_context=None):
            call_count["n"] += 1
            return _ready(_step("s1", "filesystem"))

        with patch("core.planner.planner.call", side_effect=fake_call):
            result = Iniciador().get_plan("test task")

        assert result["status"] == "ready"
        assert call_count["n"] == 1, "a valid plan must not trigger any retry"

    def test_clarification_needed_is_returned_without_validation(self):
        def fake_call(task, retry_context=None):
            return _clarification(["which file?"])

        with patch("core.planner.planner.call", side_effect=fake_call):
            result = Iniciador().get_plan("ambiguous task")

        assert result["status"] == "clarification_needed"
        assert result["questions"] == ["which file?"]

    def test_clarification_does_not_consume_contract_retry_budget(self):
        """
        Clarification and contract retry are two separate, NOT merged,
        loops (see iniciador.py module docstring). A clarification
        response must return immediately, not advance the contract
        retry attempt counter.
        """
        def fake_call(task, retry_context=None):
            return _clarification(["which project?"])

        with patch("core.planner.planner.call", side_effect=fake_call) as mocked:
            Iniciador(max_contract_retries=1).get_plan("ambiguous task")
            # Only one call was needed -- if clarification consumed the
            # contract budget incorrectly, this assertion would still
            # pass by coincidence with max_contract_retries=1, so the
            # real guard is that no exception was raised at all despite
            # a budget of exactly 1.
            assert mocked.call_count == 1


class TestContractRetrySelfCorrection:
    """
    The critical mechanism of this session: a plan that fails contract
    validation gets ONE retry with the real errors injected as context,
    and Kimi is expected to use that context to fix it.
    """

    def test_self_corrects_after_one_invalid_worker(self):
        call_count = {"n": 0}

        def fake_call(task, retry_context=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                assert retry_context is None
                return _ready(_step("s1", "worker_FAKE"))
            assert retry_context is not None
            assert "worker_FAKE" in retry_context
            return _ready(_step("s1", "worker_ts_fix"))

        with patch("core.planner.planner.call", side_effect=fake_call):
            result = Iniciador().get_plan("test")

        assert result["status"] == "ready"
        assert result["plan"].steps[0].tool_or_worker == "worker_ts_fix"
        assert call_count["n"] == 2

    def test_retry_context_carries_all_errors_from_a_multi_error_plan(self):
        """
        A single plan with two DIFFERENT error types (worker + tool)
        must produce ONE retry call carrying both -- not two separate
        retries, not a dropped error. This is what T2's "return a list"
        change exists to make possible.
        """
        call_count = {"n": 0}

        def fake_call(task, retry_context=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _ready(
                    _step("s1", "worker_FAKE1"),
                    _step("s2", "tool_fake2"),
                )
            assert "worker_FAKE1" in retry_context
            assert "tool_fake2" in retry_context
            return _ready(
                _step("s1", "worker_ts_fix"),
                _step("s2", "filesystem"),
            )

        with patch("core.planner.planner.call", side_effect=fake_call):
            result = Iniciador().get_plan("test")

        assert result["status"] == "ready"
        assert call_count["n"] == 2


class TestRetryExhaustion:
    def test_raises_plan_contract_error_group_when_retries_exhausted(self):
        def fake_call(task, retry_context=None):
            return _ready(_step("s1", "worker_NUNCA_EXISTE"))

        with patch("core.planner.planner.call", side_effect=fake_call):
            with pytest.raises(PlanContractErrorGroup) as exc_info:
                Iniciador().get_plan("test")

        assert len(exc_info.value.errors) == 1

    def test_group_does_not_lose_errors_on_exhaustion(self):
        def fake_call(task, retry_context=None):
            # Always returns the SAME two broken steps -- Kimi never
            # corrects them, forcing exhaustion.
            return _ready(
                _step("s1", "worker_FAKE1"),
                _step("s2", "tool_fake2"),
            )

        with patch("core.planner.planner.call", side_effect=fake_call):
            with pytest.raises(PlanContractErrorGroup) as exc_info:
                Iniciador().get_plan("test")

        assert len(exc_info.value.errors) == 2


class TestPlannerErrorIsNotWrappedAsContractError:
    """
    PlannerError (bad JSON / bad shape) and PlanContractError (bad
    references in an otherwise valid plan) are sibling families, not
    parent/child (see NOVA_PLANNER_LAYER_ADR.md §2.2). A persistent
    PlannerError must propagate as itself, never get coerced into a
    PlanContractErrorGroup.
    """

    def test_persistent_planner_error_propagates_unwrapped(self):
        def fake_call(task, retry_context=None):
            raise PlannerResponseError(
                "always broken json", raw_response="not json")

        with patch("core.planner.planner.call", side_effect=fake_call):
            with pytest.raises(PlannerResponseError):
                Iniciador().get_plan("test")

    def test_planner_error_retry_context_is_built_for_second_attempt(self):
        call_count = {"n": 0}

        def fake_call(task, retry_context=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                assert retry_context is None
                raise PlannerResponseError(
                    "bad json once", raw_response="garbage")
            # Second attempt: Kimi recovers
            assert retry_context is not None
            return _ready(_step("s1", "filesystem"))

        with patch("core.planner.planner.call", side_effect=fake_call):
            result = Iniciador().get_plan("test")

        assert result["status"] == "ready"
        assert call_count["n"] == 2


class TestSharedRetryBudget:
    """
    Explicit v1 simplification (NOVA_PLANNER_LAYER_ADR.md §7, open debt
    #1): PlannerError and PlanContractError share ONE retry counter.
    This test documents that behavior so it fails loudly -- not
    silently -- the day someone gives them independent budgets without
    updating this test.
    """

    def test_one_planner_error_then_one_contract_error_exhausts_budget(self):
        call_count = {"n": 0}

        def fake_call(task, retry_context=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise PlannerResponseError("bad json", raw_response="x")
            # Second attempt: valid JSON, but now a contract error --
            # with max_contract_retries=2, this is the LAST attempt.
            return _ready(_step("s1", "worker_still_fake"))

        with patch("core.planner.planner.call", side_effect=fake_call):
            with pytest.raises(PlanContractErrorGroup):
                Iniciador(max_contract_retries=2).get_plan("test")

        assert call_count["n"] == 2
