# -*- coding: utf-8 -*-
"""
物品数据库

负责加载 item_prices.csv 并提供基于约束条件的物品查询接口。
"""

import csv
import json
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from .models import CsvItem

DROP_WEIGHTS_CSV = "drop_table_weights.csv"
MERGED_DATA_CSV = "calculator_data_merged.csv"
MAP_PRIOR_HTML = "物品轮廓爆率推断器.html"
DEFAULT_MAP_CATEGORY_WEIGHTS: Dict[int, float] = {
    cat: 1.0 for cat in range(101, 111)
}
_DROP_WEIGHT_BY_ITEM: Dict[int, List[Tuple[int, int, int]]] = {}
_DROP_GRAPH: Dict[int, List[Tuple[int, float]]] = {}
_DROP_RESOLVED_CACHE: Dict[Tuple[int, Tuple[int, ...]], Dict[int, float]] = {}
_NEST_WEIGHTS: Dict[int, List[float]] = {}
_SUBMAP_PRIOR_MULT: Dict[int, Dict[int, Dict[int, float]]] = {}
_KNOWN_ITEM_IDS: Set[int] = set()

MAP_TO_TIER_NEST: Dict[int, Tuple[int, int]] = {
    2101: (101, 2001), 2102: (101, 2002), 2103: (101, 2003), 2104: (101, 2004),
    2105: (101, 2005), 2106: (101, 2006), 2107: (101, 2007),
    2201: (102, 2011), 2202: (102, 2012), 2203: (102, 2013), 2204: (102, 2014),
    2205: (102, 2015),
    2301: (103, 2021), 2302: (103, 2022), 2303: (103, 2023), 2304: (103, 2024),
    2305: (103, 2025), 2306: (103, 2026), 2307: (103, 2027), 2308: (103, 2028),
    2309: (103, 2029), 2310: (103, 2030),
    2401: (104, 2031), 2402: (104, 2032), 2403: (104, 2033), 2404: (104, 2034),
    2405: (104, 2035), 2406: (104, 2036), 2407: (104, 2037), 2408: (104, 2038),
    2409: (104, 2039), 2410: (104, 2040),
    2501: (105, 2041), 2502: (105, 2042), 2503: (105, 2043), 2504: (105, 2044),
    2505: (105, 2045), 2506: (105, 2046), 2507: (105, 2047), 2508: (105, 2048),
    2509: (105, 2049), 2510: (105, 2050),
}
TIER_REF_NEST: Dict[int, int] = {
    101: 2001,
    102: 2011,
    103: 2021,
    104: 2031,
    105: 2041,
}


def normalize_map_id(map_id: Optional[int]) -> Optional[int]:
    """将游戏日志里的等价 MapCid 归一到权重表使用的地图 ID。"""
    if map_id is None:
        return None
    if map_id in MAP_TO_TIER_NEST:
        return map_id
    # 新日志可能出现 41xx~45xx；权重表/HTML 使用对应的 21xx~25xx。
    normalized = map_id - 2000
    if normalized in MAP_TO_TIER_NEST:
        return normalized
    return map_id


def load_csv(path: str) -> Tuple[Dict[int, CsvItem], List[CsvItem]]:
    """
    解析 item_prices.csv，返回两种索引结构：

    返回值:
        index : item_id -> CsvItem  字典，用于精确 ID 查找
        items : CsvItem 列表，用于按条件过滤查找

    CSV 必须包含列: item_id, name, category_tags, shape, quality, base_value
    """
    global _KNOWN_ITEM_IDS

    index: Dict[int, CsvItem] = {}
    items: List[CsvItem] = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                tags_raw = row['category_tags'].strip()
                if not tags_raw.startswith('['):
                    tags_raw = f'[{tags_raw}]'
                tags: List[int] = json.loads(tags_raw)
                item = CsvItem(
                    item_id=int(row['item_id']),
                    name=row['name'],
                    category_tags=tags,
                    shape=int(row['shape']),
                    quality=int(row['quality']),
                    base_value=int(row['base_value']),
                )
                index[item.item_id] = item
                items.append(item)
            except Exception:
                continue
    _KNOWN_ITEM_IDS = set(index)
    base_dir = os.path.dirname(path) or "."
    load_weight_data(base_dir)
    load_map_prior_data(os.path.join(base_dir, MAP_PRIOR_HTML))
    return index, items


def load_weight_data(base_dir: str) -> Dict[int, List[Tuple[int, int, int]]]:
    """
    优先读取 HTML 工具使用的合并 CSV；不存在时回退到旧的简化权重表。

    calculator_data_merged.csv 里包含完整 DROP 图（含 MapCid/子图根），能让地图递归权重生效；
    drop_table_weights.csv 只有类别+品质直连物品池，作为兼容后备。
    """
    merged_path = os.path.join(base_dir, MERGED_DATA_CSV)
    if os.path.exists(merged_path):
        return load_drop_weights(merged_path)
    return load_drop_weights(os.path.join(base_dir, DROP_WEIGHTS_CSV))


def load_drop_weights(path: str) -> Dict[int, List[Tuple[int, int, int]]]:
    """
    解析掉落权重表，返回 item_id -> [(category, quality, weight), ...]。

    兼容两种格式：
      - calculator_data_merged.csv: record_type=DROP 的完整掉落图
      - drop_table_weights.csv: 只有 drop_id/ref_id/weight/ref_type 的简化表

    drop_id 前三位通常是类别，第四位是品质；ref_id 可能是物品 ID，也可能是下级 drop_id。
    HTML 工具里对 ref_type 不做硬过滤，而是按"ref_id 若存在物品则算物品，否则可递归展开"。
    """
    global _DROP_GRAPH, _DROP_RESOLVED_CACHE, _DROP_WEIGHT_BY_ITEM

    weights: Dict[int, List[Tuple[int, int, int]]] = {}
    graph: Dict[int, List[Tuple[int, float]]] = {}
    if not os.path.exists(path):
        _DROP_WEIGHT_BY_ITEM = weights
        _DROP_GRAPH = graph
        _DROP_RESOLVED_CACHE = {}
        return weights

    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            record_type = (row.get('record_type') or 'DROP').upper()
            if record_type != 'DROP':
                continue
            try:
                drop_id = int(row['drop_id'])
                category = drop_id // 10
                quality = drop_id % 10
                ref_id = int(row['ref_id'])
                weight = int(row['weight'])
            except (KeyError, TypeError, ValueError):
                continue
            if weight <= 0:
                continue
            graph.setdefault(drop_id, []).append((ref_id, float(weight)))
            weights.setdefault(ref_id, []).append((category, quality, weight))

    _DROP_WEIGHT_BY_ITEM = weights
    _DROP_GRAPH = graph
    _DROP_RESOLVED_CACHE = {}
    return weights


def load_map_prior_data(path: str) -> None:
    """从 HTML 工具中读取子图品质巢权重和子图池化倍率；缺失时自动回退为 1。"""
    global _NEST_WEIGHTS, _SUBMAP_PRIOR_MULT

    if not os.path.exists(path):
        _NEST_WEIGHTS = {}
        _SUBMAP_PRIOR_MULT = {}
        return

    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
        nest_match = re.search(r"const\s+NEST_W\s*=\s*(\{.*?\});", text, re.S)
        mult_match = re.search(
            r"const\s+SUBMAP_PRIOR_MULT\s*=\s*(\{.*?\});\s*const\s+SUBMAPS_BY_TIER",
            text,
            re.S,
        )
        nest_raw = json.loads(nest_match.group(1)) if nest_match else {}
        mult_raw = json.loads(mult_match.group(1)) if mult_match else {}
    except (OSError, json.JSONDecodeError, AttributeError):
        _NEST_WEIGHTS = {}
        _SUBMAP_PRIOR_MULT = {}
        return

    _NEST_WEIGHTS = {int(k): [float(x) for x in v] for k, v in nest_raw.items()}
    _SUBMAP_PRIOR_MULT = {
        int(tier): {
            int(map_id): {int(item_id): float(mult) for item_id, mult in item_map.items()}
            for map_id, item_map in maps.items()
        }
        for tier, maps in mult_raw.items()
    }


def _resolve_drop_to_items(
    drop_id: Optional[int],
    item_ids: Set[int],
) -> Dict[int, float]:
    """按 HTML 的 resolveDropToItems 逻辑递归展开 drop 图，并归一化到物品份额。"""
    if drop_id is None or not _DROP_GRAPH:
        return {}

    cache_key = (drop_id, tuple(sorted(item_ids)))
    if cache_key in _DROP_RESOLVED_CACHE:
        return _DROP_RESOLVED_CACHE[cache_key]

    out: Dict[int, float] = {}

    def dfs(cur_drop_id: int, scale: float, path_seen: Set[int]) -> None:
        edges = _DROP_GRAPH.get(cur_drop_id, [])
        total = sum(weight for _ref_id, weight in edges)
        if total <= 0:
            return
        for ref_id, weight in edges:
            child_scale = scale * (weight / total)
            if ref_id in _KNOWN_ITEM_IDS:
                if ref_id in item_ids:
                    out[ref_id] = out.get(ref_id, 0.0) + child_scale
            elif ref_id in _DROP_GRAPH and ref_id not in path_seen:
                path_seen.add(ref_id)
                dfs(ref_id, child_scale, path_seen)
                path_seen.remove(ref_id)

    dfs(drop_id, 1.0, {drop_id})
    total_out = sum(out.values())
    if total_out > 0:
        out = {item_id: weight / total_out for item_id, weight in out.items()}
    _DROP_RESOLVED_CACHE[cache_key] = out
    return out


def _nest_quality_multiplier(map_id: Optional[int], quality: int) -> float:
    """子图品质巢权重：当前子图 NEST 期望价 / 当前档参考 NEST 期望价。"""
    map_id = normalize_map_id(map_id)
    if map_id is None or map_id not in MAP_TO_TIER_NEST:
        return 1.0
    tier, nest = MAP_TO_TIER_NEST[map_id]
    ref_nest = TIER_REF_NEST.get(tier)
    cur_weights = _NEST_WEIGHTS.get(nest)
    ref_weights = _NEST_WEIGHTS.get(ref_nest) if ref_nest is not None else None
    idx = max(0, min(5, quality - 1))
    if not cur_weights or not ref_weights or idx >= len(cur_weights) or idx >= len(ref_weights):
        return 1.0
    ref_value = ref_weights[idx]
    if ref_value <= 0:
        return 1.0
    return cur_weights[idx] / ref_value


def _submap_pool_multiplier(map_id: Optional[int], item_id: int) -> float:
    """子图池化倍率：HTML 内 SUBMAP_PRIOR_MULT 中预计算的物品倍率。"""
    map_id = normalize_map_id(map_id)
    if map_id is None or map_id not in MAP_TO_TIER_NEST:
        return 1.0
    tier, _nest = MAP_TO_TIER_NEST[map_id]
    mult = _SUBMAP_PRIOR_MULT.get(tier, {}).get(map_id, {}).get(item_id)
    if mult is None or mult <= 0:
        return 1.0
    return mult


def _candidate_weight(
    item: CsvItem,
    map_category_weights: Optional[Dict[int, float]] = None,
    map_id: Optional[int] = None,
    map_drop_weights: Optional[Dict[int, float]] = None,
) -> float:
    """计算单个候选的有效出现权重；地图类别权重入口默认全为 1。"""
    category_weights = map_category_weights or DEFAULT_MAP_CATEGORY_WEIGHTS

    if map_drop_weights:
        # 完整地图 drop 图已经包含品质比例和物品池概率，不能再叠乘叶子池权重。
        base = map_drop_weights.get(item.item_id, 0.0)
        if base <= 0:
            return 0.0
        category_mult = max(
            (category_weights.get(category, 1.0) for category in item.category_tags),
            default=1.0,
        )
        return base * category_mult

    rows = _DROP_WEIGHT_BY_ITEM.get(item.item_id, [])
    total = 0.0
    for category, quality, weight in rows:
        if quality != item.quality or category not in item.category_tags:
            continue
        total += weight * category_weights.get(category, 1.0)

    base = total if total > 0 else 1.0
    return (
        base
        * _nest_quality_multiplier(map_id, item.quality)
        * _submap_pool_multiplier(map_id, item.item_id)
    )


def _weighted_est_price(
    candidates: List[CsvItem],
    map_category_weights: Optional[Dict[int, float]] = None,
    map_id: Optional[int] = None,
) -> Optional[float]:
    """按掉落权重计算候选集合的期望价格。"""
    map_id = normalize_map_id(map_id)
    map_drop_id = MAP_TO_TIER_NEST.get(map_id, (None, None))[1] if map_id is not None else None
    map_drop_weights = _resolve_drop_to_items(
        map_drop_id,
        {item.item_id for item in candidates},
    )
    weighted_sum = 0.0
    weight_sum = 0.0
    for item in candidates:
        weight = _candidate_weight(item, map_category_weights, map_id, map_drop_weights)
        weighted_sum += item.base_value * weight
        weight_sum += weight
    if weight_sum <= 0 and map_drop_weights:
        # 地图根图没有覆盖当前候选集合时，退回全局/旧权重，避免全 0。
        return _weighted_est_price(candidates, map_category_weights, None)
    if weight_sum <= 0:
        return None
    return weighted_sum / weight_sum


def candidate_probabilities(
    candidates: List[CsvItem],
    map_category_weights: Optional[Dict[int, float]] = None,
    map_id: Optional[int] = None,
) -> Dict[int, float]:
    """返回候选物品在当前约束集合内的归一化出现概率。"""
    if not candidates:
        return {}
    if len(candidates) == 1:
        return {candidates[0].item_id: 1.0}

    map_id = normalize_map_id(map_id)
    map_drop_id = MAP_TO_TIER_NEST.get(map_id, (None, None))[1] if map_id is not None else None
    map_drop_weights = _resolve_drop_to_items(
        map_drop_id,
        {item.item_id for item in candidates},
    )
    weights = {
        item.item_id: _candidate_weight(item, map_category_weights, map_id, map_drop_weights)
        for item in candidates
    }
    total = sum(weights.values())

    if total <= 0 and map_drop_weights:
        return candidate_probabilities(candidates, map_category_weights, None)
    if total <= 0:
        equal = 1.0 / len(candidates)
        return {item.item_id: equal for item in candidates}
    return {item_id: weight / total for item_id, weight in weights.items()}


def probability_source_label(candidates: List[CsvItem], map_id: Optional[int] = None) -> str:
    """说明候选概率当前使用的是地图递归权重还是全局回退权重。"""
    original_map_id = map_id
    map_id = normalize_map_id(map_id)
    if map_id is None or map_id not in MAP_TO_TIER_NEST:
        return "全局权重"
    map_drop_id = MAP_TO_TIER_NEST[map_id][1]
    resolved = _resolve_drop_to_items(map_drop_id, {item.item_id for item in candidates})
    if resolved:
        if original_map_id != map_id:
            return f"地图权重 {original_map_id}->{map_id}->{map_drop_id}"
        return f"地图权重 {map_id}->{map_drop_id}"
    return f"全局权重（地图 {original_map_id}->{map_id}->{map_drop_id} 未覆盖候选）"


def query_item(
    shape: Optional[int],
    quality: Optional[int],
    categories: Set[int],
    item_cid: Optional[int],
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    excluded_categories: Optional[Set[int]] = None,
    excluded_qualities: Optional[Set[int]] = None,
    max_shape_wh: Optional[Tuple[int, int]] = None,
    map_category_weights: Optional[Dict[int, float]] = None,
    map_id: Optional[int] = None,
) -> Tuple[Optional[CsvItem], int, bool, Optional[float], str]:
    """
    根据已知约束查询物品候选，返回最佳匹配及统计信息。

    Args:
        shape               : ItemSlotType，如 11=1×1（None=未知）
        quality             : 品质 1~6（None=未知）
        categories          : 已知类别 tag 集合（空=未知）
        item_cid            : 精确 ItemCid（None=未知）
        csv_index           : load_csv() 返回的 ID 索引
        csv_items           : load_csv() 返回的全量列表
        excluded_categories : 负向约束——确定不属于这些类别的 tag 集合
        excluded_qualities  : 负向约束——确定不是这些品质的品质值集合
        max_shape_wh        : (max_w, max_h) 推断的最大允许尺寸（shape=None 时生效）
        map_category_weights: 地图类别权重倍率入口，默认所有类别为 1.0
        map_id              : MapCid，用于叠加子图品质巢权重与子图池化倍率

    返回值:
        (best, count, unique, est_price, price_label)
        best        : 价格最高的候选 CsvItem，无候选时为 None
        count       : 候选总数
        unique      : 是否唯一确定（count == 1 或精确命中）
        est_price   : 估算价格（唯一确定时为 None）
        price_label : 估算方式说明，如 "权重价"

    过滤顺序（优先级依次降低）：
        1. ItemCid 已知 → 精确查找，直接返回
        2. 按 shape 过滤（正向）；shape=None 且 max_shape_wh 非空时按最大允许尺寸过滤
        3. 按 quality 过滤（正向）
        4. 按 excluded_qualities 过滤（负向，排除确认不是的品质）
        5. 按 categories 过滤（正向，必须含全部已知类别）
        6. 按 excluded_categories 过滤（负向，排除含有已确认不属于类别的候选）
        7. 若类别正向过滤后为空，保留 shape+quality 结果（容错）

    估算规则（多候选时）：按 drop_table_weights.csv 中的掉落权重计算期望价。
    """
    if item_cid and item_cid in csv_index:
        return csv_index[item_cid], 1, True, None, ""

    candidates = list(csv_items)

    if shape is not None:
        candidates = [i for i in candidates if i.shape == shape]
    elif max_shape_wh is not None:
        # shape 未知但可推断最大尺寸：过滤掉一定放不下的候选
        max_w, max_h = max_shape_wh
        def _fits(s: int) -> bool:
            ss = str(s)
            if len(ss) == 2:
                return int(ss[0]) <= max_w and int(ss[1]) <= max_h
            return False
        candidates = [i for i in candidates if _fits(i.shape)]

    if quality is not None:
        candidates = [i for i in candidates if i.quality == quality]

    # 负向品质约束：排除确定不是的品质
    if excluded_qualities:
        candidates = [i for i in candidates if i.quality not in excluded_qualities]

    # 正向类别约束：候选必须含有全部已知类别（每一个类别都经过全量扫描确认）
    if categories:
        with_cat = [i for i in candidates if all(c in i.category_tags for c in categories)]
        if with_cat:
            candidates = with_cat

    # 负向类别约束：排除包含已确认"不属于"类别的候选
    # 逻辑：若某类别 X 的全量扫描未命中此物品，则该物品不可能含有类别 X
    if excluded_categories:
        candidates = [
            i for i in candidates
            if not any(c in excluded_categories for c in i.category_tags)
        ]

    if not candidates:
        return None, 0, False, None, ""

    best = max(candidates, key=lambda i: i.base_value)
    count = len(candidates)
    if count == 1:
        return best, 1, True, None, ""

    est = _weighted_est_price(candidates, map_category_weights, map_id)
    label = "权重价" if est is not None else ""

    return best, count, False, est, label
