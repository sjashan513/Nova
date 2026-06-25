"""
Tests for core/planner/planner.py.

Per NOVA_CLI_MVP_ROADMAP.md §0.3, these mock requests.post directly --
one level deeper than test_iniciador.py, which mocks planner.call
itself. This is the layer that actually talks HTTP, so this is where
NIM's response shape gets exercised, not the Iniciador's retry logic
(that's test_iniciador.py's job).

No network calls happen here. The only tests that touch the real NIM
API are test_planner_live.py / test_iniciador_live.py, which are NOT
part of this suite on purpose -- they require a real NIM_API_KEY and
spend real API quota, so they must never run as a side effect of a
plain `pytest` invocation.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

import core.planner.planner as planner_mod
from core.domain.exceptions import PlannerResponseError, PlannerValidationError
from core.domain.models import Plan


@pytest.fixture(autouse=True)
def fake_api_key():
    """
    planner.call() refuses to run at all without NIM_API_KEY set (it's
    a local misconfiguration check, not a PlannerError -- see the
    RuntimeError raised in planner.call). Every test in this file
    mocks requests.post, so the key's value never matters, but it must
    be present for call() to get past that check.
    """
    with patch.dict(os.environ, {"NIM_API_KEY": "fake-key-for-tests"}):
        yield


def _fake_http_response(content_str: str):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content_str}}]
    }
    return mock_resp


VALID_PLAN_JSON = (
    '{"status": "ready", "questions": [], '
    '"plan": {"objective": "fix it", "steps": [{"id": "s1", '
    '"description": "fix", "tool_or_worker": "worker_ts_fix", "assumes": []}]}}'
)


class TestValidResponses:
    def test_ready_status_instantiates_a_plan_object(self):
        with patch("requests.post", return_value=_fake_http_response(VALID_PLAN_JSON)):
            result = planner_mod.call("fix signal.ts")

        assert result["status"] == "ready"
        assert isinstance(result["plan"], Plan)
        assert result["plan"].steps[0].tool_or_worker == "worker_ts_fix"

    def test_clarification_needed_returns_questions(self):
        clarif_json = (
            '{"status": "clarification_needed", '
            '"questions": ["which file?"], "plan": null}'
        )
        with patch("requests.post", return_value=_fake_http_response(clarif_json)):
            result = planner_mod.call("fix the bug")

        assert result["status"] == "clarification_needed"
        assert result["questions"] == ["which file?"]
        assert result["plan"] is None

    def test_tolerates_markdown_code_fences(self):
        """
        The system prompt tells Kimi not to wrap JSON in markdown
        fences, but planner.call() is tolerant of it anyway as a
        best-effort cleanup -- this is NOT a substitute for
        response_format=json_object, just defense in depth.
        """
        fenced = f"```json\n{VALID_PLAN_JSON}\n```"
        with patch("requests.post", return_value=_fake_http_response(fenced)):
            result = planner_mod.call("do something")

        assert result["status"] == "ready"
        assert isinstance(result["plan"], Plan)


class TestPlannerResponseErrorCases:
    """
    Failures that happen BEFORE a Plan object can even be attempted --
    the response isn't usable JSON, or is missing the keys the
    contract requires.
    """

    def test_completely_invalid_json_raises_planner_response_error(self):
        with patch(
            "requests.post",
            return_value=_fake_http_response("this is not json at all {{{"),
        ):
            with pytest.raises(PlannerResponseError):
                planner_mod.call("do something")

    def test_missing_status_key_raises_planner_response_error(self):
        with patch(
            "requests.post", return_value=_fake_http_response('{"foo": "bar"}')
        ):
            with pytest.raises(PlannerResponseError):
                planner_mod.call("do something")

    def test_unrecognized_status_value_raises_planner_response_error(self):
        with patch(
            "requests.post",
            return_value=_fake_http_response(
                '{"status": "maybe", "plan": null}'),
        ):
            with pytest.raises(PlannerResponseError):
                planner_mod.call("do something")

    def test_ready_without_plan_payload_raises_planner_response_error(self):
        with patch(
            "requests.post",
            return_value=_fake_http_response(
                '{"status": "ready", "questions": [], "plan": null}'
            ),
        ):
            with pytest.raises(PlannerResponseError):
                planner_mod.call("do something")

    def test_unexpected_nim_response_shape_raises_planner_response_error(self):
        """
        Simulates NIM itself returning something that doesn't even
        have the choices[0].message.content shape -- e.g. an API error
        body. This must not raise a raw KeyError/IndexError.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "error": "something went wrong upstream"}

        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(PlannerResponseError):
                planner_mod.call("do something")

    def test_raw_response_is_preserved_on_the_exception(self):
        bad_text = "this is not json at all {{{"
        with patch("requests.post", return_value=_fake_http_response(bad_text)):
            with pytest.raises(PlannerResponseError) as exc_info:
                planner_mod.call("do something")

        assert exc_info.value.raw_response == bad_text


class TestPlannerValidationErrorCases:
    """
    Failures where the top-level shape is correct (status/plan keys
    present) but the "plan" payload does not instantiate as a valid
    Plan/Step. The native pydantic.ValidationError must never escape
    unwrapped -- see NOVA_PLANNER_LAYER_ADR.md §2.5.
    """

    def test_step_missing_required_field_raises_planner_validation_error(self):
        # Step is missing "id", a required field.
        broken_json = (
            '{"status": "ready", "questions": [], '
            '"plan": {"objective": "x", "steps": [{"description": "no id", '
            '"tool_or_worker": "filesystem"}]}}'
        )
        with patch("requests.post", return_value=_fake_http_response(broken_json)):
            with pytest.raises(PlannerValidationError) as exc_info:
                planner_mod.call("do something")

        assert exc_info.value.original_error is not None

    def test_does_not_leak_raw_pydantic_validation_error(self):
        """
        The specific failure mode this exists to prevent: a raw
        pydantic.ValidationError escaping the planner layer instead of
        being wrapped. If this test ever fails with a ValidationError
        instead of catching PlannerValidationError, the wrapping logic
        in planner.call() has regressed.
        """
        from pydantic import ValidationError

        broken_json = (
            '{"status": "ready", "questions": [], '
            '"plan": {"objective": "x", "steps": [{"tool_or_worker": "filesystem"}]}}'
        )
        with patch("requests.post", return_value=_fake_http_response(broken_json)):
            try:
                planner_mod.call("do something")
                pytest.fail("expected PlannerValidationError to be raised")
            except PlannerValidationError:
                pass  # expected
            except ValidationError:
                pytest.fail(
                    "a raw pydantic.ValidationError escaped unwrapped -- "
                    "this must be caught and wrapped in PlannerValidationError"
                )


class TestRetryContextIsForwarded:
    """
    planner.call() must actually send retry_context to Kimi when the
    Iniciador provides one -- otherwise the whole self-correction
    mechanism in iniciador.py has nothing real to act on.
    """

    def test_retry_context_is_included_in_the_request_payload(self):
        captured_payload = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured_payload.update(json or {})
            return _fake_http_response(VALID_PLAN_JSON)

        with patch("requests.post", side_effect=fake_post):
            planner_mod.call(
                "fix signal.ts",
                retry_context="Step 's1' referenced an unknown worker.",
            )

        user_message = captured_payload["messages"][-1]["content"]
        assert "fix signal.ts" in user_message
        assert "unknown worker" in user_message
