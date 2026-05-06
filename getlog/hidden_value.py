# -*- coding: utf-8 -*-
"""隐藏 Q5/Q6 估值。"""

from typing import Dict, Iterable, Optional

from .item_db import candidate_probabilities, probability_source_label
from .models import CsvItem
from .hidden_models import HiddenBlockCandidate, HiddenLayoutPlan, HiddenValueEstimate


def estimate_hidden_block_value(
    *,
    shape: int,
    csv_items: Iterable[CsvItem],
    map_category_weights: Optional[Dict[int, float]] = None,
    map_id: Optional[int] = None,
) -> HiddenValueEstimate:
    """按形状筛 Q5/Q6 候选，并估算该隐藏块的品质与价格分布。"""
    candidates = [
        item
        for item in csv_items
        if item.shape == shape and item.quality in (5, 6)
    ]
    if not candidates:
        return HiddenValueEstimate(
            candidate_count=0,
            p_q5=0.0,
            p_q6=0.0,
            expected_value=None,
            min_value=None,
            max_value=None,
            red_presence_probability=0.0,
            probability_source="",
        )

    probs = candidate_probabilities(
        candidates,
        map_category_weights=map_category_weights,
        map_id=map_id,
    )
    p_q5 = 0.0
    p_q6 = 0.0
    expected = 0.0
    for item in candidates:
        prob = probs.get(item.item_id, 0.0)
        expected += item.base_value * prob
        if item.quality == 5:
            p_q5 += prob
        elif item.quality == 6:
            p_q6 += prob

    return HiddenValueEstimate(
        candidate_count=len(candidates),
        p_q5=p_q5,
        p_q6=p_q6,
        expected_value=expected,
        min_value=min(item.base_value for item in candidates),
        max_value=max(item.base_value for item in candidates),
        red_presence_probability=p_q6,
        probability_source=probability_source_label(candidates, map_id),
    )


def summarize_hidden_plan_value(blocks: Iterable[HiddenBlockCandidate]) -> HiddenValueEstimate:
    """汇总一整套隐藏方案的保守/期望/激进价值。"""
    block_list = tuple(blocks)
    if not block_list:
        return HiddenValueEstimate(
            candidate_count=0,
            p_q5=0.0,
            p_q6=0.0,
            expected_value=0.0,
            min_value=0,
            max_value=0,
            red_presence_probability=0.0,
            probability_source="",
        )

    candidate_count = sum(block.value_estimate.candidate_count for block in block_list)
    p_q5 = sum(block.value_estimate.p_q5 for block in block_list)
    p_q6 = sum(block.value_estimate.p_q6 for block in block_list)
    expected = 0.0
    min_value = 0
    max_value = 0
    no_red_prob = 1.0
    sources = []
    for block in block_list:
        value = block.value_estimate
        expected += value.expected_value or 0.0
        min_value += value.min_value or 0
        max_value += value.max_value or 0
        no_red_prob *= max(0.0, 1.0 - value.red_presence_probability)
        if value.probability_source:
            sources.append(value.probability_source)

    return HiddenValueEstimate(
        candidate_count=candidate_count,
        p_q5=p_q5,
        p_q6=p_q6,
        expected_value=expected,
        min_value=min_value,
        max_value=max_value,
        red_presence_probability=1.0 - no_red_prob,
        probability_source="; ".join(dict.fromkeys(sources)),
    )


def summarize_hidden_analysis_values(plans: Iterable[HiddenLayoutPlan]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """从所有可行方案中汇总保守/期望/激进估值。"""
    plan_list = tuple(plans)
    if not plan_list:
        return (None, None, None)
    conservative_values = [
        float(plan.value_estimate.min_value)
        for plan in plan_list
        if plan.value_estimate.min_value is not None
    ]
    expected_values = [
        plan.value_estimate.expected_value
        for plan in plan_list
        if plan.value_estimate.expected_value is not None
    ]
    aggressive_values = [
        float(plan.value_estimate.max_value)
        for plan in plan_list
        if plan.value_estimate.max_value is not None
    ]
    conservative = min(conservative_values) if conservative_values else None
    expected = (
        sum(expected_values) / len(expected_values)
        if expected_values
        else None
    )
    aggressive = max(aggressive_values) if aggressive_values else None
    return (conservative, expected, aggressive)

