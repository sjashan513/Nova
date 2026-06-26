"""
DivergenceError family — RESERVED for Fase 5
(core/director/divergence_comparator.py + core/director/reviewer.py).

Intentionally empty. Owned by core/director/. Do not populate before
Fase 5's own design session -- see NOVA_DIRECTOR_LAYER_ADR.md §4.5 for
the ReviewerVerdict contract this will eventually need to map onto.
"""

from .nova_error import NovaError


class DivergenceError(NovaError):
    """
    RESERVED for Fase 5 (divergence_comparator.py + reviewer.py).
    Intentionally empty. Owned by core/director/.
    """
