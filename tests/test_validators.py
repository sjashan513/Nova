"""
Tests for core/planner/validators.py.

The critical test here is test_detects_all_errors_in_a_single_pass --
this is the exact behavior change between Fase 0 and Fase 1
(validate_plan_against_registry used to raise on the first invalid
Step; it now walks the whole plan). Without this, the Iniciador's
bounded retry loop cannot converge whenever a plan has more than one
real error -- see NOVA_PLANNER_LAYER_ADR.md §2.1.
"""

from core.domain.models import Plan, Step
from core.domain.exceptions import WorkerNotFoundError, ToolNotFoundError
from core.planner.validators import validate_plan_against_registry


def _step(step_id: str, tool_or_worker: str) -> Step:
    return Step(id=step_id, description="test step", tool_or_worker=tool_or_worker)


class TestValidPlans:
    def test_valid_plan_returns_empty_list(self):
        plan = Plan(
            objective="fix signal.ts",
            steps=[
                _step("s1", "filesystem"),
                _step("s2", "worker_ts_fix"),
            ],
        )
        errors = validate_plan_against_registry(plan, plan_id="p1")
        assert errors == []

    def test_empty_plan_returns_empty_list(self):
        plan = Plan(objective="noop", steps=[])
        errors = validate_plan_against_registry(plan, plan_id="p1")
        assert errors == []


class TestSingleError:
    def test_unknown_worker_raises_worker_not_found(self):
        plan = Plan(objective="x", steps=[_step("s1", "worker_fake")])
        errors = validate_plan_against_registry(plan, plan_id="p1")
        assert len(errors) == 1
        assert isinstance(errors[0], WorkerNotFoundError)
        assert errors[0].step_id == "s1"
        assert errors[0].raw_value == "worker_fake"

    def test_unknown_tool_raises_tool_not_found(self):
        plan = Plan(objective="x", steps=[_step("s1", "filesystemm")])
        errors = validate_plan_against_registry(plan, plan_id="p1")
        assert len(errors) == 1
        assert isinstance(errors[0], ToolNotFoundError)
        assert errors[0].step_id == "s1"
        assert errors[0].raw_value == "filesystemm"

    def test_worker_not_found_carries_available_workers(self):
        plan = Plan(objective="x", steps=[_step("s1", "worker_fake")])
        errors = validate_plan_against_registry(plan, plan_id="p1")
        assert len(errors[0].available_workers) > 0
        assert "worker_ts_fix" in errors[0].available_workers

    def test_tool_not_found_carries_available_tools(self):
        plan = Plan(objective="x", steps=[_step("s1", "filesystemm")])
        errors = validate_plan_against_registry(plan, plan_id="p1")
        assert errors[0].available_tools == [
            "filesystem", "terminal", "git", "vscode"
        ]


class TestMultipleErrorsInOnePass:
    """
    THE critical test class for T2. A plan with several real errors,
    of different types, in non-contiguous steps, must surface ALL of
    them in a single call -- not just the first one encountered.
    """

    def test_detects_all_errors_in_a_single_pass(self):
        plan = Plan(
            objective="broken plan",
            steps=[
                _step("s1", "filesystem"),          # valid
                _step("s2", "worker_ts_fxi"),        # ERROR 1 (worker)
                _step("s3", "worker_jsdoc"),          # valid
                _step("s4", "filesystemm"),           # ERROR 2 (tool)
                _step("s5", "worker_nope"),            # ERROR 3 (worker)
            ],
        )

        errors = validate_plan_against_registry(plan, plan_id="p2")

        assert len(errors) == 3

    def test_errors_preserve_plan_order(self):
        plan = Plan(
            objective="broken plan",
            steps=[
                _step("s1", "filesystem"),
                _step("s2", "worker_ts_fxi"),
                _step("s3", "worker_jsdoc"),
                _step("s4", "filesystemm"),
                _step("s5", "worker_nope"),
            ],
        )

        errors = validate_plan_against_registry(plan, plan_id="p2")

        assert [e.step_id for e in errors] == ["s2", "s4", "s5"]

    def test_errors_have_correct_concrete_types(self):
        plan = Plan(
            objective="broken plan",
            steps=[
                _step("s2", "worker_ts_fxi"),
                _step("s4", "filesystemm"),
                _step("s5", "worker_nope"),
            ],
        )

        errors = validate_plan_against_registry(plan, plan_id="p2")

        assert isinstance(errors[0], WorkerNotFoundError)
        assert isinstance(errors[1], ToolNotFoundError)
        assert isinstance(errors[2], WorkerNotFoundError)

    def test_does_not_raise_with_multiple_errors(self):
        """
        Regression guard for the pre-Fase-1 behavior: this must NOT
        raise on the first error. The whole point of T2 is that it
        returns instead.
        """
        plan = Plan(
            objective="x",
            steps=[_step("s1", "worker_fake1"), _step("s2", "tool_fake2")],
        )
        # If this raises, the test itself fails with an unhandled
        # exception -- no need for pytest.raises here, absence of
        # raising IS the assertion.
        errors = validate_plan_against_registry(plan, plan_id="p1")
        assert len(errors) == 2
