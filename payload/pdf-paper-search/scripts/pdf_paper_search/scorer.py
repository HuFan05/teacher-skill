from __future__ import annotations


def score_breakdown(**components: float) -> dict[str, float]:
    return {key: round(float(value), 3) for key, value in components.items() if float(value) != 0.0}

