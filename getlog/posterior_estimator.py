# -*- coding: utf-8 -*-
"""Constraint-based posterior total-price estimator."""

import math
import random
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class WeightedValue:
    """One possible value for an item and its unnormalized posterior weight."""

    value: float
    weight: float
    quality: Optional[int] = None
    cells: Optional[int] = None


@dataclass(frozen=True)
class PosteriorEstimate:
    """Summary of the estimated total-value posterior."""

    estimate: float
    mean: float
    q50: float
    cv: float
    item_count: int
    unresolved_count: int
    sample_count: int


def normalize_values(values: Iterable[WeightedValue]) -> List[WeightedValue]:
    """Drop zero weights and normalize a discrete distribution."""
    clean = [
        WeightedValue(float(v.value), max(0.0, float(v.weight)), v.quality, v.cells)
        for v in values
        if v.weight > 0
    ]
    total = sum(v.weight for v in clean)
    if total <= 0:
        return []
    return [WeightedValue(v.value, v.weight / total, v.quality, v.cells) for v in clean]


def price_likelihood(candidate_value: float, observed_price: Optional[float]) -> float:
    """
    Soft value likelihood for price-derived constraints.

    Exact price hits still dominate, but non-exact rows retain a small likelihood
    so OCR/log noise does not collapse the posterior to zero.
    """
    if observed_price is None or observed_price <= 0:
        return 1.0
    scale = max(800.0, observed_price * 0.08)
    z = (candidate_value - observed_price) / scale
    return max(1.0e-9, math.exp(-0.5 * z * z))


def _weighted_mean_variance(dist: Sequence[WeightedValue]) -> Tuple[float, float]:
    mean = sum(v.value * v.weight for v in dist)
    var = sum(((v.value - mean) ** 2) * v.weight for v in dist)
    return mean, max(0.0, var)


def estimate_total_posterior(
    item_distributions: Sequence[Sequence[WeightedValue]],
    *,
    sample_count: int = 4096,
    seed: int = 20260507,
    target_quality: Optional[int] = None,
    target_count: Optional[int] = None,
    target_cells: Optional[int] = None,
    target_constraints: Optional[Sequence[Tuple[Optional[int], Optional[int]]]] = None,
) -> PosteriorEstimate:
    """
    Estimate total value from independent item posterior distributions.

    Small distributions are summarized analytically for mean/cv. q50 is estimated
    by deterministic Monte Carlo so the UI remains responsive even with many
    candidate pools.
    """
    distributions = [
        normalize_values(dist)
        for dist in item_distributions
    ]
    distributions = [dist for dist in distributions if dist]
    unresolved_count = len(item_distributions) - len(distributions)
    if not distributions:
        return PosteriorEstimate(
            estimate=0.0,
            mean=0.0,
            q50=0.0,
            cv=0.0,
            item_count=0,
            unresolved_count=unresolved_count,
            sample_count=0,
        )

    if target_quality is not None and (
        target_count is not None
        or target_cells is not None
        or target_constraints is not None
    ):
        constraints = (
            list(target_constraints)
            if target_constraints is not None
            else [(target_count, target_cells)]
        )
        return _estimate_total_posterior_with_quality_constraints(
            distributions,
            target_quality=target_quality,
            target_constraints=constraints,
            sample_count=sample_count,
            seed=seed,
            unresolved_count=unresolved_count,
        )

    means_vars = [_weighted_mean_variance(dist) for dist in distributions]
    mean_total = sum(mean for mean, _var in means_vars)
    var_total = sum(var for _mean, var in means_vars)
    cv = math.sqrt(var_total) / mean_total if mean_total > 0 else 0.0

    if len(distributions) == 1:
        q50 = _weighted_quantile(distributions[0], 0.5)
        actual_samples = 0
    else:
        rng = random.Random(seed)
        totals: List[float] = []
        cumulative = []
        for dist in distributions:
            acc = 0.0
            rows = []
            for value in dist:
                acc += value.weight
                rows.append((acc, value.value))
            if rows:
                rows[-1] = (1.0, rows[-1][1])
            cumulative.append(rows)

        for _ in range(sample_count):
            total = 0.0
            for rows in cumulative:
                pick = rng.random()
                for threshold, value in rows:
                    if pick <= threshold:
                        total += value
                        break
            totals.append(total)
        totals.sort()
        mid = len(totals) // 2
        if len(totals) % 2:
            q50 = totals[mid]
        else:
            q50 = (totals[mid - 1] + totals[mid]) / 2.0
        actual_samples = len(totals)

    # q50 is robust for skewed high-value pools; cv adds a bounded upside
    # correction so very uncertain red/gold pools are not systematically low.
    estimate = q50 * (1.0 + min(0.25, cv * 0.15))
    return PosteriorEstimate(
        estimate=estimate,
        mean=mean_total,
        q50=q50,
        cv=cv,
        item_count=len(distributions),
        unresolved_count=unresolved_count,
        sample_count=actual_samples,
    )


def _weighted_quantile(dist: Sequence[WeightedValue], q: float) -> float:
    rows = sorted(dist, key=lambda item: item.value)
    acc = 0.0
    for item in rows:
        acc += item.weight
        if acc >= q:
            return item.value
    return rows[-1].value if rows else 0.0


def _estimate_total_posterior_with_quality_constraints(
    distributions: Sequence[Sequence[WeightedValue]],
    *,
    target_quality: int,
    target_constraints: Sequence[Tuple[Optional[int], Optional[int]]],
    sample_count: int,
    seed: int,
    unresolved_count: int,
) -> PosteriorEstimate:
    """Sample total values conditioned on exact quality count/cells constraints."""
    constraints = [
        (count, cells)
        for count, cells in target_constraints
        if (count is None or count >= 0) and (cells is None or cells >= 0)
    ]
    if not constraints:
        return _empty_estimate(unresolved_count)

    n = len(distributions)
    options_by_dist = [
        _quality_options(dist, target_quality)
        for dist in distributions
    ]
    suffix: List[dict[Tuple[int, int], float]] = [dict() for _ in range(n + 1)]
    suffix[n][(0, 0)] = 1.0
    for idx in range(n - 1, -1, -1):
        cur = suffix[idx]
        for add_count, add_cells, opt_weight, _branch in options_by_dist[idx]:
            if opt_weight <= 0:
                continue
            for (tail_count, tail_cells), tail_prob in suffix[idx + 1].items():
                key = (tail_count + add_count, tail_cells + add_cells)
                cur[key] = cur.get(key, 0.0) + opt_weight * tail_prob

    allowed_states = [
        state
        for state in suffix[0]
        if _matches_gold_constraints(state, constraints)
    ]
    total_condition_prob = sum(suffix[0].get(state, 0.0) for state in allowed_states)
    if total_condition_prob <= 0:
        return _empty_estimate(unresolved_count)

    rng = random.Random(seed)
    samples: List[float] = []
    for _ in range(sample_count):
        used_count = 0
        used_cells = 0
        total = 0.0
        for idx, options in enumerate(options_by_dist):
            weighted_options = []
            for add_count, add_cells, opt_weight, branch in options:
                next_count = used_count + add_count
                next_cells = used_cells + add_cells
                finish_prob = sum(
                    suffix[idx + 1].get(
                        (final_count - next_count, final_cells - next_cells),
                        0.0,
                    )
                    for final_count, final_cells in allowed_states
                    if final_count >= next_count and final_cells >= next_cells
                )
                branch_weight = opt_weight * finish_prob
                if branch_weight > 0:
                    weighted_options.append(
                        (add_count, add_cells, branch_weight, branch)
                    )
            add_count, add_cells, _branch_weight, branch = _choose_weighted_option(
                weighted_options,
                rng,
            )
            used_count += add_count
            used_cells += add_cells
            total += _sample_branch_value(branch, rng)
        samples.append(total)

    return _summarize_samples(
        samples,
        item_count=len(distributions),
        unresolved_count=unresolved_count,
    )


def _quality_options(
    dist: Sequence[WeightedValue],
    target_quality: int,
) -> List[Tuple[int, int, float, Sequence[WeightedValue]]]:
    non_target = [value for value in dist if value.quality != target_quality]
    options: List[Tuple[int, int, float, Sequence[WeightedValue]]] = []
    non_weight = sum(value.weight for value in non_target)
    if non_weight > 0:
        options.append((0, 0, non_weight, non_target))

    by_cells: dict[int, List[WeightedValue]] = {}
    for value in dist:
        if value.quality != target_quality:
            continue
        cells = int(value.cells or 0)
        by_cells.setdefault(cells, []).append(value)
    for cells, branch in by_cells.items():
        weight = sum(value.weight for value in branch)
        if weight > 0:
            options.append((1, cells, weight, branch))
    return options


def _matches_gold_constraints(
    state: Tuple[int, int],
    constraints: Sequence[Tuple[Optional[int], Optional[int]]],
) -> bool:
    count, cells = state
    return any(
        (want_count is None or count == want_count)
        and (want_cells is None or cells == want_cells)
        for want_count, want_cells in constraints
    )


def _choose_weighted_option(
    options: Sequence[Tuple[int, int, float, Sequence[WeightedValue]]],
    rng: random.Random,
) -> Tuple[int, int, float, Sequence[WeightedValue]]:
    total_weight = sum(option[2] for option in options)
    if total_weight <= 0:
        return (0, 0, 0.0, ())
    pick = rng.random() * total_weight
    acc = 0.0
    for option in options:
        acc += option[2]
        if pick <= acc:
            return option
    return options[-1]


def _sample_branch_value(branch: Sequence[WeightedValue], rng: random.Random) -> float:
    total_weight = sum(max(0.0, value.weight) for value in branch)
    if total_weight <= 0:
        return 0.0
    pick = rng.random() * total_weight
    acc = 0.0
    for value in branch:
        acc += max(0.0, value.weight)
        if pick <= acc:
            return value.value
    return branch[-1].value if branch else 0.0


def _summarize_samples(
    samples: Sequence[float],
    *,
    item_count: int,
    unresolved_count: int,
) -> PosteriorEstimate:
    if not samples:
        return _empty_estimate(unresolved_count)
    rows = sorted(samples)
    mean = sum(rows) / len(rows)
    var = sum((value - mean) ** 2 for value in rows) / len(rows)
    cv = math.sqrt(var) / mean if mean > 0 else 0.0
    mid = len(rows) // 2
    q50 = rows[mid] if len(rows) % 2 else (rows[mid - 1] + rows[mid]) / 2.0
    estimate = q50 * (1.0 + min(0.25, cv * 0.15))
    return PosteriorEstimate(
        estimate=estimate,
        mean=mean,
        q50=q50,
        cv=cv,
        item_count=item_count,
        unresolved_count=unresolved_count,
        sample_count=len(rows),
    )


def _empty_estimate(unresolved_count: int = 0) -> PosteriorEstimate:
    return PosteriorEstimate(
        estimate=0.0,
        mean=0.0,
        q50=0.0,
        cv=0.0,
        item_count=0,
        unresolved_count=unresolved_count,
        sample_count=0,
    )
