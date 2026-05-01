#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鉴影道具组合分析。

默认场景：排除 Q1-Q4，只分析 Q5-Q6 物品；枚举 10 个鉴影道具中任选 5 个，
输出每个道具的覆盖/排除/类别重叠，以及每个 5 件组合能提供多少明确过滤信息。

用法:
  python analyze_item_tools.py
  python analyze_item_tools.py --qualities 5 6 --choose 5 --high-value 100000
  python analyze_item_tools.py --top 30 --details
"""

import argparse
import itertools
import os
import sys
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from getlog.constants import CATEGORY_NAMES, CSV_PATH, ITEM_TOOLS
from getlog.item_db import load_csv
from getlog.models import CsvItem


Tool = Tuple[int, int, str]  # (category, item_cid, tool_name)


def _tool_rows() -> List[Tool]:
    rows = [
        (category, item_cid, name)
        for item_cid, (_skill_cid, name, category) in ITEM_TOOLS.items()
    ]
    return sorted(rows, key=lambda x: x[0])


def _cat_name(category: int) -> str:
    return CATEGORY_NAMES.get(category, str(category))


def _fmt_categories(categories: Iterable[int]) -> str:
    cats = sorted(categories)
    return "/".join(_cat_name(c) for c in cats) if cats else "-"


def _filter_items(
    items: Sequence[CsvItem],
    qualities: Set[int],
) -> List[CsvItem]:
    return [item for item in items if item.quality in qualities]


def _avg(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return float(values[mid])
    return (values[mid - 1] + values[mid]) / 2


def _print_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    str_rows = [[str(cell) for cell in row] for row in rows]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in str_rows))
        for i in range(len(headers))
    ]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in str_rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def category_metrics(
    items: Sequence[CsvItem],
    tools: Sequence[Tool],
    high_value: int,
) -> List[dict]:
    total_items = len(items)
    total_value = sum(item.base_value for item in items)
    rows = []
    for category, item_cid, tool_name in tools:
        hit_items = [item for item in items if category in item.category_tags]
        miss_items = [item for item in items if category not in item.category_tags]
        hit_high = [item for item in hit_items if item.base_value >= high_value]
        overlap_categories = {
            c
            for item in hit_items
            for c in item.category_tags
            if c != category
        }
        rows.append({
            'category': category,
            'item_cid': item_cid,
            'tool_name': tool_name,
            'hit_items': len(hit_items),
            'hit_pct': len(hit_items) / total_items * 100 if total_items else 0.0,
            'miss_items': len(miss_items),
            'hit_value': sum(item.base_value for item in hit_items),
            'hit_value_pct': (
                sum(item.base_value for item in hit_items) / total_value * 100
                if total_value else 0.0
            ),
            'hit_high': len(hit_high),
            'overlap_count': len(overlap_categories),
            'overlaps': overlap_categories,
        })
    return rows


def _signature(item: CsvItem, selected_categories: Sequence[int]) -> Tuple[int, ...]:
    return tuple(c for c in selected_categories if c in item.category_tags)


def combination_metrics(
    items: Sequence[CsvItem],
    combo: Sequence[Tool],
    high_value: int,
) -> dict:
    selected_categories = tuple(category for category, _item_cid, _name in combo)
    buckets: Dict[Tuple[int, ...], List[CsvItem]] = {}
    for item in items:
        buckets.setdefault(_signature(item, selected_categories), []).append(item)

    covered = [
        item for item in items
        if any(c in item.category_tags for c in selected_categories)
    ]
    uncovered = len(items) - len(covered)
    high_items = [item for item in items if item.base_value >= high_value]
    covered_high = [item for item in high_items if item in covered]
    exact_items = [
        bucket[0] for bucket in buckets.values()
        if len(bucket) == 1
    ]
    exact_high = [item for item in exact_items if item.base_value >= high_value]
    bucket_sizes = [len(bucket) for bucket in buckets.values()]

    selected_value = sum(item.base_value for item in covered)
    exact_value = sum(item.base_value for item in exact_items)
    high_value_hit = sum(item.base_value for item in covered_high)

    overlap_pairs = 0
    overlap_items = 0
    for item in items:
        hits = sum(1 for c in selected_categories if c in item.category_tags)
        if hits >= 2:
            overlap_items += 1
            overlap_pairs += hits * (hits - 1) // 2

    # 排序分数：优先覆盖高价值，其次唯一识别高价值，再看整体唯一识别和候选收缩。
    score = (
        len(covered_high) * 1_000_000
        + len(exact_high) * 100_000
        + len(exact_items) * 1_000
        + len(covered) * 10
        - int(_avg(bucket_sizes) * 10)
    )

    return {
        'categories': selected_categories,
        'tool_names': tuple(name for _cat, _cid, name in combo),
        'covered': len(covered),
        'uncovered': uncovered,
        'covered_pct': len(covered) / len(items) * 100 if items else 0.0,
        'covered_high': len(covered_high),
        'total_high': len(high_items),
        'exact': len(exact_items),
        'exact_high': len(exact_high),
        'selected_value': selected_value,
        'exact_value': exact_value,
        'high_value_hit': high_value_hit,
        'signature_count': len(buckets),
        'avg_bucket': _avg(bucket_sizes),
        'median_bucket': _median(bucket_sizes),
        'max_bucket': max(bucket_sizes) if bucket_sizes else 0,
        'overlap_items': overlap_items,
        'overlap_pairs': overlap_pairs,
        'score': score,
    }


def analyze(
    csv_path: str,
    qualities: Set[int],
    choose: int,
    high_value: int,
    top: int,
    show_details: bool,
) -> None:
    _csv_index, csv_items = load_csv(csv_path)
    items = _filter_items(csv_items, qualities)
    tools = _tool_rows()
    if not items:
        raise RuntimeError("筛选后没有物品，请检查 --qualities 参数。")
    if not (1 <= choose <= len(tools)):
        raise RuntimeError(f"--choose 必须在 1..{len(tools)} 之间。")

    total_value = sum(item.base_value for item in items)
    high_items = [item for item in items if item.base_value >= high_value]

    print("鉴影道具组合分析")
    print(f"CSV: {csv_path}")
    print(
        f"品质范围: {','.join('Q' + str(q) for q in sorted(qualities))} "
        f"(已排除 {','.join('Q' + str(q) for q in range(1, 7) if q not in qualities)})"
    )
    print(
        f"物品数: {len(items)}  总价值: ¥{total_value:,}  "
        f"高价值阈值: ¥{high_value:,}  高价值物品: {len(high_items)}"
    )
    print()

    print("单个鉴影道具指标")
    cat_rows = category_metrics(items, tools, high_value)
    _print_table(
        ["类别", "道具", "命中", "命中%", "排除", "命中高价", "价值覆盖%", "重叠类别"],
        [
            [
                _cat_name(row['category']),
                row['tool_name'],
                row['hit_items'],
                f"{row['hit_pct']:.1f}%",
                row['miss_items'],
                row['hit_high'],
                f"{row['hit_value_pct']:.1f}%",
                _fmt_categories(row['overlaps']),
            ]
            for row in sorted(cat_rows, key=lambda r: (-r['hit_high'], -r['hit_value'], r['category']))
        ],
    )
    print()

    combos = [
        combination_metrics(items, combo, high_value)
        for combo in itertools.combinations(tools, choose)
    ]
    combos.sort(
        key=lambda row: (
            -row['covered_high'],
            -row['exact_high'],
            -row['exact'],
            row['avg_bucket'],
            -row['covered'],
            -row['selected_value'],
        )
    )

    print(f"Top {min(top, len(combos))} 组合（{len(tools)} 选 {choose}）")
    _print_table(
        [
            "排名", "选择类别", "覆盖", "覆盖高价", "唯一", "唯一高价",
            "平均候选", "最大候选", "重叠物品", "覆盖价值",
        ],
        [
            [
                idx,
                _fmt_categories(row['categories']),
                f"{row['covered']}/{len(items)} ({row['covered_pct']:.1f}%)",
                f"{row['covered_high']}/{row['total_high']}",
                row['exact'],
                row['exact_high'],
                f"{row['avg_bucket']:.2f}",
                row['max_bucket'],
                row['overlap_items'],
                f"¥{row['selected_value']:,}",
            ]
            for idx, row in enumerate(combos[:top], start=1)
        ],
    )

    if show_details and combos:
        print()
        best = combos[0]
        print("最佳组合详情")
        print("道具: " + " / ".join(best['tool_names']))
        print("类别: " + _fmt_categories(best['categories']))
        print(
            f"签名数: {best['signature_count']}  "
            f"平均候选: {best['avg_bucket']:.2f}  "
            f"中位候选: {best['median_bucket']:.2f}  "
            f"最大候选: {best['max_bucket']}"
        )


def main() -> None:
    if sys.platform == 'win32':
        import io as _io
        sys.stdout = _io.TextIOWrapper(
            sys.stdout.buffer, encoding='utf-8', errors='replace',
            line_buffering=True,
        )
    else:
        sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description="分析 Q1-Q4 排除后，鉴影道具 10 选 5 的过滤价值。",
    )
    parser.add_argument('--csv', default=CSV_PATH, help=f'物品 CSV 路径（默认: {CSV_PATH}）')
    parser.add_argument(
        '--qualities',
        nargs='+',
        type=int,
        default=[5, 6],
        help='纳入分析的品质，默认 5 6（即排除 Q1-Q4）',
    )
    parser.add_argument('--choose', type=int, default=5, help='选择几个鉴影道具，默认 5')
    parser.add_argument('--high-value', type=int, default=100_000, help='高价值阈值，默认 100000')
    parser.add_argument('--top', type=int, default=20, help='输出前 N 个组合，默认 20')
    parser.add_argument('--details', action='store_true', help='输出最佳组合的补充详情')
    args = parser.parse_args()

    csv_path = args.csv
    if not os.path.exists(csv_path):
        raise SystemExit(f"找不到 CSV 文件: {csv_path}")

    analyze(
        csv_path=csv_path,
        qualities=set(args.qualities),
        choose=args.choose,
        high_value=args.high_value,
        top=args.top,
        show_details=args.details,
    )


if __name__ == '__main__':
    main()
