

from .nova_error import NovaError
from typing import Optional

# ---------------------------------------------------------------------------
# PlannerError family — Fase 1. Owned by the Iniciador.
#
# Covers everything that can go wrong with Kimi's raw response BEFORE a
# valid Plan object exists. Sequential to, not a parent/child of,
# PlanContractError below.
# ---------------------------------------------------------------------------


class PlannerError(NovaError):
    """
    Umbrella for failures in the Planner's raw response, before a Plan
    object can be said to exist. Raised by core/planner/planner.py,
    caught by the Iniciador's retry loop in core/planner/iniciador.py.
    """

    def __init__(self, message: str, raw_response: Optional[str] = None):
        self.raw_response = raw_response
        super().__init__(message)


class PlannerResponseError(PlannerError):
    """
    Kimi's response is not parseable as JSON, or parses but is missing
    the expected top-level keys ("status", "plan"). This is a format
    failure -- it says nothing yet about whether the plan itself, if
    any, is well-formed.
    """

    def __init__(self, message: str, raw_response: Optional[str] = None):
        super().__init__(message, raw_response)


class PlannerValidationError(PlannerError):
    """
    Kimi's response has the correct top-level shape (status/plan keys
    present), but the content of "plan" does not instantiate as a valid
    Plan/Step. Wraps the native pydantic.ValidationError -- callers
    should never see a raw pydantic error escape the planner layer.
    """

    def __init__(
        self,
        message: str,
        raw_response: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        self.original_error = original_error
        super().__init__(message, raw_response)
