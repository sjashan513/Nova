"""
Root of Nova's error vocabulary.

NovaError itself is never raised directly -- every real failure raises
one of the family-specific subclasses defined in this package's other
modules (planner_errors.py, contract_errors.py, execution_errors.py,
divergence_errors.py). Kept in its own tiny module so every family
module can import just the root, without pulling in any sibling
family's code.
"""


class NovaError(Exception):
    """Root of all domain-specific errors in Nova. Never raised directly."""
