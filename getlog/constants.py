# -*- coding: utf-8 -*-
"""
常量与映射表

包含所有游戏内固定数据（技能ID、类别ID、地图技能描述等），
以及若干格式化工具函数。
"""

import os
import sys
from typing import Dict, Set, Tuple

# ─── 路径默认值 ────────────────────────────────────────────────────────────

def resource_path(relative_path: str) -> str:
    """返回源码目录或 PyInstaller 临时目录中的资源文件路径。"""
    base_path = getattr(sys, '_MEIPASS', os.getcwd())
    return os.path.join(base_path, relative_path)


def default_game_log_path() -> str:
    """根据当前 Windows 用户动态定位 BidKing 的 Player.log。"""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        appdata_dir = os.path.dirname(local_appdata)
        return os.path.join(appdata_dir, "LocalLow", "laolin", "BidKing", "Player.log")
    else:
        user_dir = os.path.expanduser("~")
        return os.path.join(user_dir, "AppData", "LocalLow", "laolin", "BidKing", "Player.log")


DEFAULT_GAME_LOG = default_game_log_path()
LOCAL_LOG = "Player.log"
LOCAL_COPY_LOG = "Player - 副本.log"
CSV_PATH = resource_path("item_prices.csv")

# ─── 英雄技能映射 ──────────────────────────────────────────────────────────

# 艾莎英雄技能 SkillCid → 扫描到的品质上限
HERO_SKILL_QUALITY: Dict[int, int] = {
    1001034: 1,
    1001033: 2,
    1001032: 3,
    1001031: 4,
}

# ─── 道具映射 ──────────────────────────────────────────────────────────────

# 道具 ItemCid → (触发技能 SkillCid, 道具中文名, 揭示的类别 tag)
ITEM_TOOLS: Dict[int, Tuple[int, str, int]] = {
    100151: (2001, "家具物品鉴影", 101),
    100152: (2002, "医疗药品鉴影", 102),
    100153: (2003, "时尚潮流鉴影", 103),
    100154: (2004, "兵装军火鉴影", 104),
    100155: (2005, "珠宝矿藏鉴影", 105),
    100156: (2006, "文物古董鉴影", 106),
    100157: (2007, "数码娱乐鉴影", 107),
    100158: (2008, "能源交通鉴影", 108),
    100159: (2009, "食饮珍馐鉴影", 109),
    100160: (2010, "书画古籍鉴影", 110),
}

# 技能 SkillCid → 揭示的类别 tag（由 ITEM_TOOLS 反向推导）
SKILL_TO_CATEGORY: Dict[int, int] = {v[0]: v[2] for v in ITEM_TOOLS.values()}

# ─── 类别映射 ──────────────────────────────────────────────────────────────

# 类别 tag → 中文名
CATEGORY_NAMES: Dict[int, str] = {
    101: "家具物品", 102: "医疗药品", 103: "时尚潮流", 104: "兵装军火",
    105: "珠宝矿藏", 106: "文物古董", 107: "数码娱乐", 108: "能源交通",
    109: "食饮珍馐", 110: "书画古籍",
}

# ─── 地图技能 ──────────────────────────────────────────────────────────────

# 地图技能 SkillCid → 描述（未收录的 SkillCid 在输出时显示"未知地图技能"）
MAP_SKILL_DESC: Dict[int, str] = {
    200001: "品质4物品轮廓+位置",
    200002: "地图初始化技能",
    200005: "全场各类别每格均价",
    200010: "紫色(Q=4)物品数量",
    200021: "随机揭示X件物品完整信息",
    200027: "随机揭示X件物品品质",
    200032: "品质X物品均价",
}

# 地图技能中哪些 SkillCid 可以强制设定 HitBoxList 中物品的品质
MAP_SKILL_FORCE_QUALITY: Dict[int, int] = {
    200001: 4,   # 该技能只命中品质=4的物品
}

# ─── 输出分隔符 ────────────────────────────────────────────────────────────

SEP  = "=" * 64
THIN = "-" * 64

# ─── 格式化工具函数 ────────────────────────────────────────────────────────

def fmt_shape(slot_type: int) -> str:
    """将 ItemSlotType 整数转为可读形状字符串，如 11→1x1, 22→2x2, 12→1x2。"""
    s = str(slot_type)
    if len(s) == 2:
        return f"{s[0]}x{s[1]}"
    return str(slot_type)


def fmt_categories(cats: Set[int]) -> str:
    """将类别 tag 集合转为中文名字符串，如 {101, 103} → '家具物品/时尚潮流'。"""
    return "/".join(CATEGORY_NAMES.get(c, str(c)) for c in sorted(cats))


def fmt_price(v: int) -> str:
    """整数价格格式化为千分位字符串，如 12345 → '12,345'。"""
    return f"{v:,}"
