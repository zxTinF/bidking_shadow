# -*- coding: utf-8 -*-
"""
日志条目处理器

从各类技能日志列表中提取结构化事件，同时更新 GameState 中的物品知识。

三个处理函数均遵循相同约定：
  - 入参 logs             : 日志中的列表字段（HeroSkillLog / MapSkillLog / ItemSkillLog）
  - 入参 state            : 当前 GameState，函数会就地更新 state.items
  - 入参 target_cast_round: 仅处理 CastRound 等于该值的条目；None 表示初始扫描
  - 返回                  : 结构化事件列表，供 renderer 格式化输出
"""

from typing import List, Optional

from .constants import (
    HERO_SKILL_QUALITY,
    ITEM_TOOLS,
    MAP_SKILL_FORCE_QUALITY,
    SKILL_TO_CATEGORY,
)
from .models import GameState


def _common_skill_stats(entry: dict) -> dict:
    """提取技能日志里跨英雄/地图/道具共用的统计字段。"""
    return {
        'hit_count':     entry.get('HitItemIndex'),
        'total_hit':     entry.get('TotalHitBoxIndex'),
        'avg_price':     entry.get('AllHitItemAvgPrice'),
        'avg_box_price': entry.get('AllHitBoxAvgPrice'),
        'total_price':   entry.get('HitItemTotalPrice'),
        'avg_box_count': entry.get('AllHitItemAvgBoxIndex'),
        'item_types':    entry.get('HitItemTypeList', []),
    }


def process_hero_skill_log(
    logs: List[dict],
    state: GameState,
    target_cast_round: Optional[int],
) -> List[dict]:
    """
    处理 HeroSkillLog（艾莎英雄扫描技能）。

    对所有 SkillCid（包括未收录的未知技能）尽可能提取 HitBoxList 中的
    shape / quality / categories / item_cid / price 等字段；已知艾莎技能额外
    用 SkillCid 补充品质并记录负向品质约束。

    事件字段:
        type         : 'hero_skill'
        skill_cid    : 技能 ID
        quality      : 品质上限（None=未知技能）
        cast_round   : 发生回合（None=初始扫描）
        uids         : 本次扫描到的物品 UID 列表
        hit_count 等 : 日志中的聚合统计字段（若存在）
    """
    events = []
    for entry in logs:
        cr = entry.get('CastRound')
        if cr != target_cast_round:
            continue
        skill_cid = entry.get('SkillCid', 0)
        quality = HERO_SKILL_QUALITY.get(skill_cid)
        revealed_uids = []
        for box in entry.get('HitBoxList', []):
            uid = box.get('ItemUid', '')
            if not uid:
                continue
            k = state.get_or_create(uid)
            k.update_from_box(box)
            if quality is not None:
                k.quality = quality
            revealed_uids.append(uid)
        # 全量品质扫描负向约束：
        # 英雄技能扫描全场该品质的物品，未命中的物品必然不是该品质
        if quality is not None:
            state.record_scan('quality', quality, set(revealed_uids))

        ev = {
            'type': 'hero_skill',
            'skill_cid': skill_cid,
            'quality': quality,
            'cast_round': cr,
            'uids': revealed_uids,
        }
        ev.update(_common_skill_stats(entry))
        if revealed_uids or any(v is not None and v != [] for v in _common_skill_stats(entry).values()):
            events.append(ev)
    return events


def process_map_skill_log(
    logs: List[dict],
    state: GameState,
    target_cast_round: Optional[int],
) -> List[dict]:
    """
    处理 MapSkillLog（地图技能）。

    对所有 SkillCid（包括未收录的未知技能）尽可能提取：
      - HitBoxList 中的物品属性（shape/quality/item_cid 等）
      - 聚合统计（命中数、均价、总价等）

    事件字段:
        type          : 'map_skill'
        skill_cid     : 技能 ID
        cast_round    : 发生回合
        raw           : 原始 JSON 条目
        uids          : HitBoxList 中解析到的物品 UID 列表
        hit_count     : HitItemIndex（命中物品数）
        total_hit     : TotalHitBoxIndex（影响格数）
        avg_price     : AllHitItemAvgPrice
        avg_box_price : AllHitBoxAvgPrice
        total_price   : HitItemTotalPrice
        avg_box_count : AllHitItemAvgBoxIndex
        item_types    : HitItemTypeList（类别 tag 列表）
    """
    events = []
    for entry in logs:
        cr = entry.get('CastRound')
        if cr != target_cast_round:
            continue
        skill_cid = entry.get('SkillCid', 0)

        ev: dict = {
            'type': 'map_skill',
            'skill_cid': skill_cid,
            'cast_round': cr,
            'raw': entry,
            'uids': [],
        }
        ev.update(_common_skill_stats(entry))

        force_quality = MAP_SKILL_FORCE_QUALITY.get(skill_cid)
        for box in entry.get('HitBoxList', []):
            uid = box.get('ItemUid', '')
            if not uid:
                continue
            k = state.get_or_create(uid)
            k.update_from_box(box)
            if force_quality is not None:
                k.quality = force_quality
            ev['uids'].append(uid)

        events.append(ev)
    return events


def process_item_skill_log(
    logs: List[dict],
    state: GameState,
    target_cast_round: int,
    check_dup: bool = True,
) -> List[dict]:
    """
    处理 ItemSkillLog（玩家使用鉴影道具）。

    Args:
        check_dup : True 时跳过 state.displayed_event_uids 中已记录的事件，
                    防止 S2C_37 汇总报文与 S2C_39 实时推送重复显示。

    事件字段:
        type       : 'item_skill'
        skill_cid  : 道具触发的技能 ID
        item_cid   : 道具 ItemCid
        tool_name  : 道具中文名
        category   : 揭示的类别 tag（None=未知）
        cast_round : 使用回合
        uids       : 被鉴影的物品 UID 列表
        event_uid  : 日志事件自身的 Uid（用于去重）
    """
    events = []
    for entry in logs:
        cr = entry.get('CastRound')
        if cr != target_cast_round:
            continue
        event_uid = entry.get('Uid', '')
        if check_dup and event_uid and event_uid in state.displayed_event_uids:
            continue

        skill_cid = entry.get('SkillCid', 0)
        item_cid  = entry.get('ItemCid', 0)
        tool_info = ITEM_TOOLS.get(item_cid)
        category  = SKILL_TO_CATEGORY.get(skill_cid)
        if category is None and tool_info:
            category = tool_info[2]
        revealed_uids = []

        for box in entry.get('HitBoxList', []):
            uid = box.get('ItemUid', '')
            if not uid:
                continue
            k = state.get_or_create(uid)
            k.update_from_box(box)   # 内含 BoxId 可靠性判断
            if category:
                k.categories.add(category)
            revealed_uids.append(uid)

        if event_uid:
            state.displayed_event_uids.add(event_uid)

        # 全量类别扫描负向约束：
        # 鉴影道具扫描全场该类别的物品，未命中的物品必然不含该类别
        if category is not None:
            state.record_scan('category', category, set(revealed_uids))

        ev = {
            'type':       'item_skill',
            'skill_cid':  skill_cid,
            'item_cid':   item_cid,
            'tool_name':  tool_info[1] if tool_info else f"道具{item_cid}",
            'category':   category,
            'cast_round': cr,
            'uids':       revealed_uids,
            'event_uid':  event_uid,
        }
        ev.update(_common_skill_stats(entry))
        events.append(ev)
    return events
