"""
Work-weighted sortition — seeded, reproducible committee draws.

A draw selects ``seats`` members for a committee where each member's chance of selection
is proportional to their :func:`work_weight` over the org's configured window. It is:

  * **Weighted** — more issued earnings ⇒ higher selection probability.
  * **Deterministic** — the algorithm is driven only by ``random.Random(seed)`` seeded
    with the draw's ``seed`` (never global/unseeded randomness), so the same seed over the
    same weights always reproduces the exact same committee, in the same order.
  * **Auditable** — the seed AND the weight snapshot used are stored in
    ``SortitionDraw.result``, so anyone can re-run the draw and confirm the outcome
    without trusting the operator (:func:`verify_draw`).

Selection is weighted sampling **without replacement**: pick one member proportional to
weight, remove them, repeat until the seats are filled. Members with zero weight have zero
probability while any positive weight remains; only if seats still need filling after the
positive-weight pool is exhausted are zero-weight members drawn (uniformly, still seeded).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List

from django.db import transaction

from apps.orgs.models import Membership
from apps.orgs.weighting import work_weight_map

from .models import SortitionDraw

ZERO = Decimal("0")


class SortitionError(Exception):
    """A draw could not be run (bad seat count, no members, etc.)."""


def weighted_sample_without_replacement(
    seed: str, weights: Dict[int, Decimal], k: int
) -> List[int]:
    """Deterministically draw ``k`` distinct keys, weight-proportional, without replacement.

    Args:
        seed: the seed string; the same seed + weights always yields the same result.
        weights: ``{key: weight}`` (weights are Decimal, coerced to float for sampling).
        k: number of keys to draw (capped at ``len(weights)``).

    Returns:
        The drawn keys, in selection order.

    The pool is iterated in a fixed order (sorted keys) so the only source of variation is
    the seeded RNG — a hard requirement for reproducibility.
    """
    rng = random.Random(seed)
    pool = sorted(weights.keys())  # stable, deterministic ordering
    remaining_weight = {key: float(weights[key]) for key in pool}
    k = min(k, len(pool))

    selected: List[int] = []
    for _ in range(k):
        keys = [key for key in pool if key not in selected]
        total = sum(remaining_weight[key] for key in keys)
        if total > 0:
            # Weighted pick: walk the cumulative distribution over the (ordered) keys.
            r = rng.random() * total
            upto = 0.0
            chosen = keys[-1]  # fallback guards float rounding at the tail
            for key in keys:
                upto += remaining_weight[key]
                if r < upto:
                    chosen = key
                    break
        else:
            # No positive weight left — fall back to a seeded uniform pick over the rest.
            chosen = keys[rng.randrange(len(keys))]
        selected.append(chosen)
    return selected


@dataclass
class DrawResult:
    """The outcome of a draw: who was selected + the exact weights used (reproducible)."""

    seats: int
    seed: str
    window: str
    weights: Dict[str, str]  # {str(membership_id): str(weight)} — the snapshot used
    selected: List[int]  # membership ids, in selection order
    total_weight: Decimal = ZERO
    eligible_count: int = 0


def _compute(org, seats: int, window: str, seed: str) -> DrawResult:
    """Pure computation: build the weight snapshot and run the seeded draw."""
    weights = work_weight_map(org, window)
    if not weights:
        raise SortitionError("No members to draw from in this org.")
    selected = weighted_sample_without_replacement(seed, weights, seats)
    return DrawResult(
        seats=seats,
        seed=seed,
        window=window,
        weights={str(mid): str(w) for mid, w in weights.items()},
        selected=selected,
        total_weight=sum(weights.values(), ZERO),
        eligible_count=len(weights),
    )


@transaction.atomic
def run_draw(org, seats: int, window: str, seed: str) -> SortitionDraw:
    """Run and persist a draw. The full snapshot + seed are stored for reproducibility."""
    if seats < 1:
        raise SortitionError("A draw needs at least one seat.")
    if not (seed or "").strip():
        raise SortitionError("A draw needs a seed.")
    result = _compute(org, seats, window, seed.strip())
    return SortitionDraw.objects.create(
        org=org,
        seats=seats,
        weight_window=window,
        seed=seed.strip(),
        result={
            "seats": result.seats,
            "seed": result.seed,
            "window": result.window,
            "weights": result.weights,
            "selected": result.selected,
            "total_weight": str(result.total_weight),
            "eligible_count": result.eligible_count,
        },
    )


def reproduce(draw: SortitionDraw) -> List[int]:
    """Re-run the selection from the draw's STORED seed + STORED weight snapshot.

    Uses only what was persisted (not a fresh weight computation), so verification is
    independent of any earnings that changed after the draw — it proves the stored result
    follows deterministically from the stored inputs.
    """
    stored = draw.result or {}
    weights = {int(mid): Decimal(w) for mid, w in (stored.get("weights") or {}).items()}
    return weighted_sample_without_replacement(
        stored.get("seed", draw.seed), weights, stored.get("seats", draw.seats)
    )


def verify_draw(draw: SortitionDraw) -> bool:
    """True iff re-running the stored seed over the stored weights reproduces the result."""
    stored_selected = (draw.result or {}).get("selected", [])
    return reproduce(draw) == stored_selected


@dataclass
class SelectedSeat:
    """A drawn seat: the member plus the weight they were drawn on (from the snapshot)."""

    seat: int  # 1-based selection order
    membership: Membership
    weight: str  # the snapshot weight string, exactly as stored


def selected_members(draw: SortitionDraw) -> List[Membership]:
    """Resolve the drawn membership ids to Membership rows, in selection order."""
    ids = (draw.result or {}).get("selected", [])
    by_id = {
        m.id: m for m in Membership.objects.filter(org=draw.org, id__in=ids).select_related("user")
    }
    return [by_id[i] for i in ids if i in by_id]


def selected_seats(draw: SortitionDraw) -> List[SelectedSeat]:
    """The drawn committee as SelectedSeat rows (member + drawn-on weight), in order."""
    stored = draw.result or {}
    ids = stored.get("selected", [])
    weights = stored.get("weights", {})
    by_id = {
        m.id: m for m in Membership.objects.filter(org=draw.org, id__in=ids).select_related("user")
    }
    seats: List[SelectedSeat] = []
    for position, mid in enumerate(ids, start=1):
        if mid in by_id:
            seats.append(
                SelectedSeat(
                    seat=position, membership=by_id[mid], weight=weights.get(str(mid), "0")
                )
            )
    return seats
