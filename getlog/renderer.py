# -*- coding: utf-8 -*-
"""
格式化输出

负责将结构化事件和游戏状态渲染为可读的控制台文本。

主要函数：
  - fmt_item_line          : 格式化单行物品信息
  - calc_total_price        : 计算估算总价格（约束后验）
  - print_all_items_snapshot: 输出全量物品快照
  - print_events            : 批量输出事件列表
  - print_bids              : 输出某回合出价情况
  - print_catchup_summary   : tail 模式追赶完成后输出当前快照
"""

from typing import Dict, List, Optional

from .constants import (
    CATEGORY_NAMES,
    ITEM_TOOLS,
    MAP_SKILL_DESC,
    SEP,
    THIN,
    fmt_categories,
    fmt_price,
    fmt_shape,
)
from .item_db import (
    candidate_probabilities,
    query_item,
    query_item_floor_value,
    _filter_candidates,
)
from .models import CsvItem, GameState, ItemKnowledge
from .posterior_estimator import (
    WeightedValue,
    estimate_total_posterior,
    price_likelihood,
)


# ─── 单行物品格式化 ────────────────────────────────────────────────────────

def fmt_item_line(
    uid: str,
    k: ItemKnowledge,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    map_category_weights: Optional[Dict[int, float]] = None,
    map_id: Optional[int] = None,
) -> str:
    """
    将一件物品的已知信息格式化为单行输出。

    左侧显示已知属性（BoxId / 形状 / 品质 / 类别），
    右侧显示查询结果（唯一确定 / 多候选 / 无匹配）。

    多候选时额外显示按掉落权重计算的期望价。
    """
    best, count, unique, est, label = query_item(
        k.shape, k.quality, k.categories, k.item_cid, csv_index, csv_items,
        k.excluded_categories, k.excluded_qualities,
        map_category_weights=map_category_weights,
        map_id=map_id,
    )

    info_parts = []
    box_id_val = k.box_id if k.box_id is not None else 0
    info_parts.append(f"BoxId={box_id_val:<3}")
    if k.shape is not None:
        info_parts.append(f"形状:{fmt_shape(k.shape)}")
    if k.quality is not None:
        info_parts.append(f"品质:{k.quality}")
    if k.categories:
        info_parts.append(f"[{fmt_categories(k.categories)}]")
    left = "  ".join(info_parts)

    if k.price is not None and k.item_cid:
        name = csv_index[k.item_cid].name if k.item_cid in csv_index else f"CID={k.item_cid}"
        right = f"=> [已知] {name}  ¥{fmt_price(k.price)}"
    elif best:
        if unique:
            right = f"=> [唯一] {best.name}  ¥{fmt_price(best.base_value)}"
        else:
            quality_val = k.quality if k.quality is not None else 0
            if quality_val in (1, 2, 3, 4) and est is not None:
                right = (
                    f"=> [{count}候选] {label}:¥{est:.0f}  "
                    f"最高: {best.name} ¥{fmt_price(best.base_value)}"
                )
            else:
                right = f"=> [{count}候选] 最高: {best.name}  ¥{fmt_price(best.base_value)}"
    else:
        right = "=> 无匹配"

    return f"    {left:<45} {right}"


# ─── 总价估算 ──────────────────────────────────────────────────────────────

def calc_total_price(
    state: GameState,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    map_category_weights: Optional[Dict[int, float]] = None,
) -> float:
    """Constraint posterior estimate used as the displayed total estimate."""
    distributions = []
    for k in state.items.values():
        if k.price is not None and k.item_cid:
            distributions.append([WeightedValue(float(k.price), 1.0)])
            continue
        candidates = _filter_candidates(
            k.shape,
            k.quality,
            k.categories,
            k.item_cid,
            csv_index,
            csv_items,
            k.excluded_categories,
            k.excluded_qualities,
        )
        if not candidates:
            distributions.append([])
            continue
        if len(candidates) == 1:
            distributions.append([WeightedValue(float(candidates[0].base_value), 1.0)])
            continue
        probs = candidate_probabilities(
            candidates,
            map_category_weights=map_category_weights,
            map_id=state.map_id,
        )
        observed_price = float(k.price) if k.price is not None else None
        distributions.append(
            [
                WeightedValue(
                    float(item.base_value),
                    probs.get(item.item_id, 0.0)
                    * price_likelihood(float(item.base_value), observed_price),
                )
                for item in candidates
            ]
        )
    return estimate_total_posterior(distributions, sample_count=2048).estimate


# ─── 物品快照 ──────────────────────────────────────────────────────────────

def calc_total_floor_price(
    state: GameState,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
) -> float:
    """Calculate floor total value under current constraints."""
    total = 0.0
    for k in state.items.values():
        if k.price is not None and k.item_cid:
            total += k.price
            continue
        floor = query_item_floor_value(
            k.shape, k.quality, k.categories, k.item_cid, csv_index, csv_items,
            k.excluded_categories, k.excluded_qualities,
        )
        if floor is not None:
            total += floor
    return total


def print_all_items_snapshot(
    state: GameState,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    out,
) -> None:
    """
    输出当前全部已知物品的快照（按 BoxId 升序排列），末尾附估算总价格。
    state.items 为空时不输出任何内容。
    """
    if not state.items:
        return
    sorted_items = sorted(
        state.items.items(),
        key=lambda x: (x[1].box_id if x[1].box_id is not None else 0, x[0]),
    )
    total = calc_total_price(state, csv_index, csv_items)
    floor_total = calc_total_floor_price(state, csv_index, csv_items)
    print(
        f"\n  ┌─ 当前全部物品 ({len(sorted_items)} 件) "
        "─────────────────────────────────",
        file=out,
    )
    for uid, k in sorted_items:
        print(fmt_item_line(uid, k, csv_index, csv_items, map_id=state.map_id), file=out)
    print(
        f"  └─ 估算总价格: ¥{total:,.0f} ──────────",
        file=out,
    )
    print(
        f"  Floor total: CNY {floor_total:,.0f} (min possible per item under constraints)",
        file=out,
    )


def _format_skill_stats(ev: dict) -> str:
    """格式化各类技能事件共用的聚合统计字段。"""
    stats_parts = []
    if ev.get('hit_count') is not None:
        stats_parts.append(f"命中{ev['hit_count']}件")
    if ev.get('total_hit') is not None:
        stats_parts.append(f"影响格数:{ev['total_hit']}")
    if ev.get('avg_price') is not None:
        stats_parts.append(f"均价:{ev['avg_price']:.1f}")
    if ev.get('avg_box_price') is not None:
        stats_parts.append(f"每格均价:{ev['avg_box_price']:.2f}")
    if ev.get('total_price') is not None:
        stats_parts.append(f"总价:{ev['total_price']}")
    if ev.get('avg_box_count') is not None:
        stats_parts.append(f"件均格数:{ev['avg_box_count']:.1f}")
    if ev.get('item_types'):
        type_str = " / ".join(
            CATEGORY_NAMES.get(t, str(t)) for t in ev['item_types']
        )
        stats_parts.append(f"类别:[{type_str}]")
    return "  " + "  ".join(stats_parts) if stats_parts else ""


# ─── 事件批量输出 ──────────────────────────────────────────────────────────

def print_events(
    events: List[dict],
    state: GameState,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    out,
) -> None:
    """
    将处理器返回的事件列表格式化输出。
    每个技能/道具事件后附全量物品快照（有物品信息更新时）。
    """
    for ev in events:
        etype = ev['type']

        if etype == 'hero_skill':
            skill_cid = ev['skill_cid']
            q = ev['quality']
            cr = ev.get('cast_round')
            round_tag = f"第{cr}回合执行" if cr is not None else "初始扫描"
            q_tag = f"品质<={q}" if q else ""
            desc_parts = [part for part in (q_tag, round_tag) if part]
            desc = ", ".join(desc_parts)
            stats_str = _format_skill_stats(ev)
            print(f"\n  [英雄技能 {skill_cid}] ({desc}){stats_str}", file=out)
            for uid in ev['uids']:
                k = state.items.get(uid)
                if k:
                    print(fmt_item_line(uid, k, csv_index, csv_items), file=out)
            print_all_items_snapshot(state, csv_index, csv_items, out)

        elif etype == 'map_skill':
            skill_cid = ev['skill_cid']
            cr = ev.get('cast_round')
            round_tag = f"第{cr}回合" if cr is not None else "初始"
            desc = MAP_SKILL_DESC.get(skill_cid, "未知地图技能")

            stats_str = _format_skill_stats(ev)

            print(f"\n  [地图技能 {skill_cid}] ({round_tag}) {desc}{stats_str}", file=out)

            if ev['uids']:
                for uid in ev['uids']:
                    k = state.items.get(uid)
                    if k:
                        print(fmt_item_line(uid, k, csv_index, csv_items), file=out)
                print_all_items_snapshot(state, csv_index, csv_items, out)

        elif etype == 'item_skill':
            cr = ev.get('cast_round', '?')
            cat_name = CATEGORY_NAMES.get(ev.get('category', 0), "")
            cat_tag = f"  类别:{cat_name}" if cat_name else ""
            stats_str = _format_skill_stats(ev)
            print(
                f"\n  [道具 {ev['item_cid']} {ev['tool_name']}] 第{cr}回合使用"
                f"{cat_tag}{stats_str} => 命中{len(ev['uids'])}件:",
                file=out,
            )
            for uid in ev['uids']:
                k = state.items.get(uid)
                if k:
                    print(fmt_item_line(uid, k, csv_index, csv_items), file=out)
            print_all_items_snapshot(state, csv_index, csv_items, out)


# ─── 出价信息 ──────────────────────────────────────────────────────────────

def print_bids(state: GameState, round_num: int, out) -> None:
    """输出某回合所有玩家的出价及道具使用情况（无出价数据时静默跳过）。"""
    lines = []
    for p_uid, p in state.players.items():
        price = p['prices'].get(round_num)
        if price is not None:
            used_cid = p['items_used'].get(round_num)
            tool_str = ""
            if used_cid and used_cid in ITEM_TOOLS:
                tool_str = f"  [使用了{ITEM_TOOLS[used_cid][1]}]"
            lines.append(f"    {p['name']}: ¥{fmt_price(price)}{tool_str}")
    if lines:
        label = "初始出价" if round_num == 0 else f"第{round_num}回合出价"
        print(f"\n  [{label}]", file=out)
        print("\n".join(lines), file=out)


# ─── 追赶快照 ──────────────────────────────────────────────────────────────

def print_catchup_summary(
    state: GameState,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    out,
) -> None:
    """
    tail 模式中读到 EOF（追赶完成）时，若对局仍在进行，
    输出当前对局快照（对局信息 + 全部已知物品 + 各回合出价）。
    """
    print(f"\n{SEP}", file=out)
    print(f"  [追赶完成] 对局进行中 — 第 {state.current_round} 回合", file=out)
    print(f"  对局ID: {state.uid}   地图: {state.map_id}", file=out)
    players_str = "  vs  ".join(
        f"{p['name']}(英雄{p['hero_cid']})" for p in state.players.values()
    )
    print(f"  玩家: {players_str}", file=out)
    print(SEP, file=out)

    print_all_items_snapshot(state, csv_index, csv_items, out)

    all_rounds = set()
    for p in state.players.values():
        all_rounds.update(p['prices'].keys())
    for r in sorted(all_rounds):
        print_bids(state, r, out)

    print(f"\n{THIN}", file=out)
    print(f"  等待第 {state.current_round} 回合新事件...", file=out)
    print(THIN, file=out)
