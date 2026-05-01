# -*- coding: utf-8 -*-
"""
S2C 事件处理器

每个函数对应一类服务器推送消息，负责：
  1. 从 JSON 数据中提取字段，更新 GameState
  2. 调用处理器（processors）解析技能/道具日志
  3. 调用渲染器（renderer）输出可读文本

函数命名规则: handle_s2c<编号>，与协议消息类型对应。
"""

from typing import Dict, List

from .constants import ITEM_TOOLS, SEP, SKILL_TO_CATEGORY, THIN
from .models import CsvItem, GameState
from .processors import (
    process_hero_skill_log,
    process_item_skill_log,
    process_map_skill_log,
)
from .renderer import (
    print_all_items_snapshot,
    print_bids,
    print_events,
)


def handle_s2c33(
    data: dict,
    state: GameState,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    out,
) -> None:
    """
    S2C_33_game_start_notify — 游戏开始（第 1 回合）。

    初始化 state 的对局 ID、地图、玩家信息，
    处理初始扫描（CastRound 为 None 的技能日志），
    输出第 1 回合标题、玩家列表及初始出价。
    """
    gd = data.get('GameData', {})
    state.uid = gd.get('Uid', '')
    state.map_id = gd.get('MapId', 0)
    state.current_round = 1
    state.update_players(gd.get('UserLog', []))

    print(f"\n{SEP}", file=out)
    print(f"  第 1 回合  [游戏开始]", file=out)
    print(f"  对局ID: {state.uid}   地图: {state.map_id}", file=out)
    players_str = "  vs  ".join(
        f"{p['name']}(英雄{p['hero_cid']})" for p in state.players.values()
    )
    print(f"  玩家: {players_str}", file=out)
    print(SEP, file=out)

    # 初始扫描：target_cast_round=None 表示 CastRound 字段不存在
    events = []
    events += process_hero_skill_log(gd.get('HeroSkillLog', []), state, None)
    events += process_map_skill_log(gd.get('MapSkillLog', []), state, None)
    print_events(events, state, csv_index, csv_items, out)

    print_bids(state, 0, out)
    print(f"\n{THIN}", file=out)
    out.flush()


def handle_s2c37(
    data: dict,
    state: GameState,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    out,
) -> None:
    """
    S2C_37_game_next_round_notify — 进入下一回合。

    data.GameData.Round 记录的是「刚结束的回合」编号，
    因此新回合 = Round + 1。
    处理刚结束回合产生的全部技能/道具事件，输出结算信息。
    """
    gd = data.get('GameData', {})
    completed_round: int = gd.get('Round', 1)
    new_round: int = completed_round + 1
    state.current_round = new_round
    state.update_players(gd.get('UserLog', []))

    print(f"\n{SEP}", file=out)
    print(f"  第 {new_round} 回合  [第{completed_round}回合结算完毕]", file=out)
    print(SEP, file=out)

    events = []
    events += process_hero_skill_log(gd.get('HeroSkillLog', []), state, completed_round)
    events += process_map_skill_log(gd.get('MapSkillLog', []), state, completed_round)
    events += process_item_skill_log(gd.get('ItemSkillLog', []), state, completed_round)
    print_events(events, state, csv_index, csv_items, out)

    print_bids(state, completed_round, out)
    print(f"\n{THIN}", file=out)
    out.flush()


def handle_s2c39(
    data: dict,
    state: GameState,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    out,
) -> None:
    """
    S2C_39_game_use_item — 实时道具使用通知。

    该消息是单条实时推送，不经过 CastRound 过滤，
    直接按 Uid 去重，防止后续 S2C_37 汇总时重复输出。
    """
    logs = data.get('ItemSkillLog', [])
    if not logs:
        return

    shown_header = False
    for entry in logs:
        event_uid = entry.get('Uid', '')
        if event_uid and event_uid in state.displayed_event_uids:
            continue

        skill_cid = entry.get('SkillCid', 0)
        item_cid  = entry.get('ItemCid', 0)
        cr        = entry.get('CastRound', state.current_round)
        tool_info = ITEM_TOOLS.get(item_cid)
        category  = SKILL_TO_CATEGORY.get(skill_cid)
        if category is None and tool_info:
            category = tool_info[2]

        revealed_uids: List[str] = []
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

        # 全量类别扫描负向约束（与 process_item_skill_log 逻辑一致）
        if category is not None:
            state.record_scan('category', category, set(revealed_uids))

        if not shown_header:
            print(f"\n  [实时道具通知]", file=out)
            shown_header = True

        ev = {
            'type':       'item_skill',
            'skill_cid':  skill_cid,
            'item_cid':   item_cid,
            'tool_name':  tool_info[1] if tool_info else f"道具{item_cid}",
            'category':   category,
            'cast_round': cr,
            'uids':       revealed_uids,
            'hit_count':     entry.get('HitItemIndex'),
            'total_hit':     entry.get('TotalHitBoxIndex'),
            'avg_price':     entry.get('AllHitItemAvgPrice'),
            'avg_box_price': entry.get('AllHitBoxAvgPrice'),
            'total_price':   entry.get('HitItemTotalPrice'),
            'avg_box_count': entry.get('AllHitItemAvgBoxIndex'),
            'item_types':    entry.get('HitItemTypeList', []),
        }
        print_events([ev], state, csv_index, csv_items, out)

    if shown_header:
        out.flush()


def handle_s2c45(
    data: dict,
    state: GameState,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    out,
) -> None:
    """
    S2C_45_game_over_notify — 游戏结束。

    结束包里 StockContainer.StockBoxes 会揭晓最终答案，但 UI 需要保留结束前
    最后一轮的推断结果，方便和游戏内最终揭晓进行人工比对。因此这里不再把
    StockBoxes 写回 state.items。
    """
    winner_uid = data.get('WinUserUid', '')
    gd = data.get('GameData', {})
    last_round: int = gd.get('Round', state.current_round)
    state.update_players(gd.get('UserLog', []))

    # 最后一回合道具使用（可能未经 S2C_39 显示）
    events_item = process_item_skill_log(
        gd.get('ItemSkillLog', []), state, last_round, check_dup=True
    )

    winner_name = winner_uid
    for p_uid, p in state.players.items():
        if p_uid == winner_uid:
            winner_name = p['name']
            break

    print(f"\n{SEP}", file=out)
    print(f"  游戏结束", file=out)
    print(SEP, file=out)
    print(f"\n  获胜者: {winner_name}  (UID: {winner_uid})", file=out)

    if events_item:
        print(f"\n  [第{last_round}回合道具使用]", file=out)
        print_events(events_item, state, csv_index, csv_items, out)

    print_bids(state, last_round, out)

    print("\n  [全部物品揭晓] 已跳过写入，保留结束前最后一轮推断结果", file=out)

    print(f"\n{SEP}", file=out)
    out.flush()
