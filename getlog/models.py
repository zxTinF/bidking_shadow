# -*- coding: utf-8 -*-
"""
数据模型

定义三个核心数据结构：
  - CsvItem        : item_prices.csv 中一行物品记录
  - ItemKnowledge  : 游戏运行时对某件物品的累积已知信息
  - GameState      : 一局游戏的完整状态
"""

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple


# ─── CSV 物品记录 ──────────────────────────────────────────────────────────

@dataclass
class CsvItem:
    """item_prices.csv 中的单行物品信息。"""
    item_id: int
    name: str
    category_tags: List[int]   # 可以属于多个类别
    shape: int                 # ItemSlotType，如 11=1×1, 22=2×2
    quality: int               # 1~6
    base_value: int            # 基础价格


# ─── 物品知识 ──────────────────────────────────────────────────────────────

@dataclass
class ItemKnowledge:
    """
    游戏中对一件物品的累积已知信息。

    各字段来源：
      uid                 - 游戏内唯一标识，日志中的 ItemUid
      box_id              - 在地图格子中的位置 BoxId（默认 0）
      shape               - 来自 ItemSlotType（英雄/地图技能揭示）
      quality             - 来自 ItemQuility（英雄/地图技能揭示）
      categories          - 来自 ItemType（道具/地图技能揭示），可多个
      item_cid            - 精确物品 ID（地图技能 200021 或游戏结束揭示）
      price               - 精确价格（地图技能 200021 揭示）
      excluded_categories - 负向约束：确定该物品不属于这些类别
                            （全量类别扫描未命中 → 不可能是该类别）
      excluded_qualities  - 负向约束：确定该物品不是这些品质
                            （全量品质扫描未命中 → 不可能是该品质）
    """
    uid: str
    box_id: Optional[int] = None
    # box_id_confirmed=True 表示 BoxId 是精确的顶左格坐标（来自含 ItemSlotType 的扫描）
    # box_id_confirmed=False 表示 BoxId 来自"只知品质"的扫描，是占格内的随机格，不可靠
    box_id_confirmed: bool = False
    shape: Optional[int] = None
    quality: Optional[int] = None
    categories: Set[int] = field(default_factory=set)
    item_cid: Optional[int] = None
    price: Optional[int] = None
    # 手动候选确认：仅作为 UI 推断锚点，不覆盖日志真实字段
    manual_confirm_item_id: Optional[int] = None
    excluded_categories: Set[int] = field(default_factory=set)
    excluded_qualities: Set[int] = field(default_factory=set)

    def update_from_box(self, box: dict) -> None:
        """
        从日志中的 HitBoxList 单元格数据更新已知信息。

        BoxId 可靠性规则：
          - 当 box 包含 ItemSlotType（形状已知）时，BoxId 是物品的顶左格，可信 → 总是更新并标记 confirmed
          - 当 box 不含 ItemSlotType（仅揭示品质等）时，BoxId 是占格内的随机格
            → 仅在尚未有可信 BoxId 时才接受，防止覆盖已经正确的顶左角坐标

        ItemUid 是物品的永久标识，BoxId 随扫描精度而可能变化，以最精确的为准。
        """
        has_shape = 'ItemSlotType' in box
        new_bid = box.get('BoxId', 0)

        if has_shape:
            # 含形状 → BoxId 是准确的顶左格，无条件采用并标记可信
            self.box_id = new_bid
            self.box_id_confirmed = True
            self.shape = box['ItemSlotType']
        elif not self.box_id_confirmed:
            # 无形状且尚无可信坐标 → 暂时采用，后续有含形状的扫描再覆盖
            self.box_id = new_bid

        if 'ItemQuility' in box:
            self.quality = box['ItemQuility']
        if 'ItemCid' in box:
            self.item_cid = box['ItemCid']
        if 'ItemPrice' in box:
            self.price = box['ItemPrice']
        if 'ItemType' in box:
            for t in box['ItemType']:
                self.categories.add(t)


# ─── 游戏状态 ──────────────────────────────────────────────────────────────

class GameState:
    """
    一局游戏的完整状态。

    在 S2C_33 收到时初始化，随各事件递增更新，
    在 S2C_45 结束后由 run() 重置为新的 GameState。
    """

    def __init__(self) -> None:
        self.uid: str = ""           # 对局唯一 ID
        self.map_id: int = 0

        # uid -> { name, hero_cid, prices: {round->price}, items_used: {round->item_cid} }
        self.players: Dict[str, dict] = {}

        # ItemUid -> ItemKnowledge
        self.items: Dict[str, ItemKnowledge] = {}

        self.current_round: int = 1

        # 已输出的 ItemSkillLog 事件 Uid，防止 S2C_39 与 S2C_37 重复显示
        self.displayed_event_uids: Set[str] = set()

        # 全量扫描历史，用于对后续新发现的物品追溯应用负向约束
        # 每条记录: ('category'|'quality', 值, 命中UID集合)
        self._scan_history: List[Tuple[str, int, FrozenSet[str]]] = []

    def get_or_create(self, uid: str) -> ItemKnowledge:
        """
        取出或新建指定 UID 的 ItemKnowledge。
        新建时自动将历史全量扫描的负向约束追溯应用到该物品。
        """
        if uid not in self.items:
            k = ItemKnowledge(uid=uid)
            for scan_type, value, hit_uids in self._scan_history:
                if uid not in hit_uids:
                    if scan_type == 'category':
                        k.excluded_categories.add(value)
                    else:
                        k.excluded_qualities.add(value)
            self.items[uid] = k
        return self.items[uid]

    def record_scan(self, scan_type: str, value: int, hit_uids: Set[str]) -> None:
        """
        记录一次全量扫描，并立即更新所有已知物品的负向约束。

        Args:
            scan_type : 'category'（道具鉴影）或 'quality'（英雄技能）
            value     : 扫描揭示的类别 tag 或品质值
            hit_uids  : 本次扫描命中的物品 UID 集合

        凡不在 hit_uids 中的已知物品，均会被打上对应的排除标记：
          - 'category' 扫描未命中 → excluded_categories.add(value)
          - 'quality'  扫描未命中 → excluded_qualities.add(value)
        同时保存到 _scan_history，供后续新发现物品追溯使用。
        """
        frozen = frozenset(hit_uids)
        self._scan_history.append((scan_type, value, frozen))
        for uid, k in self.items.items():
            if uid not in frozen:
                if scan_type == 'category':
                    k.excluded_categories.add(value)
                else:
                    k.excluded_qualities.add(value)

    def update_players(self, user_logs: List[dict]) -> None:
        """
        从 UserLog 数组更新玩家出价和道具使用记录。
        合并而非覆盖，保留历史数据。
        """
        for u in user_logs:
            p_uid = u.get('UserUid', '')
            if not p_uid:
                continue
            if p_uid not in self.players:
                self.players[p_uid] = {
                    'name': u.get('Name', p_uid),
                    'hero_cid': u.get('HeroCid', 0),
                    'prices': {},
                    'items_used': {},
                }
            p = self.players[p_uid]
            p['name'] = u.get('Name', p['name'])
            for pl in u.get('PriceLog', []):
                r = pl.get('Round', 0)   # Round=0 表示初始出价
                p['prices'][r] = pl.get('ItemCidOrPrice', 0)
            for ul in u.get('UseItemLog', []):
                r = ul.get('Round', 0)
                p['items_used'][r] = ul.get('ItemCidOrPrice', 0)
