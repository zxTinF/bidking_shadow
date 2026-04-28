# BidKing 游戏日志解析器 — 项目架构文档

> 本文档面向后续开发者和 AI，详细说明项目的目标、数据来源、模块设计和扩展方式。

---

## 1. 项目目标

本工具解析 BidKing 游戏产生的 `Player.log` 文件，逐回合提取关键信息，结合 `item_prices.csv` 物品数据库，识别场上每件物品的可能名称与价格，并以可读文本格式实时或批量输出。

**核心输出内容：**
- 每回合的玩家出价
- 英雄技能/地图技能/道具使用所揭示的物品属性
- 根据已知属性（形状、品质、类别）查询物品候选名称和价格
- 估算全场物品总价值

---

## 2. 输入文件说明

### 2.1 Player.log

游戏客户端实时写入的文本日志，每行一条记录。关键行格式：

```
[Network] OnHanderNotify <连接标识> : (<事件类型>)<JSON数据>
```

**四类关键事件：**

| 事件类型 | 含义 | 触发时机 |
|---|---|---|
| `S2C_33_game_start_notify` | 游戏开始 | 进入第 1 回合 |
| `S2C_37_game_next_round_notify` | 回合结算 | 每回合结束后 |
| `S2C_39_game_use_item` | 实时道具使用 | 玩家使用道具的瞬间 |
| `S2C_45_game_over_notify` | 游戏结束 | 最终结算 |

**日志内 JSON 的核心子结构：**

```
GameData
├── Uid           对局唯一 ID
├── MapId         地图 ID
├── Round         回合编号（S2C_37/S2C_45 中为刚结束的回合）
├── UserLog[]     玩家列表，含 PriceLog（出价记录）和 UseItemLog（道具记录）
├── HeroSkillLog[]  英雄技能日志
├── MapSkillLog[]   地图技能日志
├── ItemSkillLog[]  道具技能日志
└── StockContainer  游戏结束时揭晓的完整物品容器
```

**HitBoxList（技能命中的物品格子）通用字段：**

| 字段 | 含义 |
|---|---|
| `ItemUid` | 物品运行时唯一 ID |
| `BoxId` | 格子位置编号（0 表示未知） |
| `ItemSlotType` | 形状，如 `11`=1×1, `22`=2×2, `12`=1×2 |
| `ItemQuility` | 品质 1~6 |
| `ItemType` | 类别 tag 列表，如 `[101]` |
| `ItemCid` | 物品配置 ID（精确 ID，部分技能才有） |
| `ItemPrice` | 物品价格（精确价格，部分技能才有） |

### 2.2 item_prices.csv

物品静态数据库，列含义：

| 列名 | 含义 |
|---|---|
| `item_id` | 物品配置 ID（对应 `ItemCid`） |
| `name` | 物品中文名称 |
| `category_tags` | 类别 tag 数组，如 `[101]` |
| `shape` | 形状，同 `ItemSlotType` |
| `quality` | 品质 1~6 |
| `base_value` | 基础价格 |
| `grid_size` | 占格数（目前未使用） |

---

## 3. 模块结构

```
e:\game\getLog\
├── parse_log.py          # CLI 入口（薄包装）
├── pyproject.toml        # 项目配置（uv/pip）
├── item_prices.csv       # 物品静态数据库
├── Player.log            # 游戏日志（运行时读取）
├── ARCHITECTURE.md       # 本文档
└── getlog/               # 核心业务包
    ├── __init__.py
    ├── constants.py      # 常量与格式化工具
    ├── models.py         # 数据模型
    ├── item_db.py        # CSV 加载与物品查询
    ├── log_parser.py     # 日志行解析与迭代
    ├── processors.py     # 技能/道具日志处理器
    ├── renderer.py       # 格式化输出
    ├── handlers.py       # S2C 事件处理器
    └── runner.py         # 主运行循环
```

### 3.1 模块职责一览

```
parse_log.py
  └─ 解析 CLI 参数，配置 stdout 编码，调用 runner.run()

getlog/constants.py
  ├─ HERO_SKILL_QUALITY     英雄技能 SkillCid → 品质上限
  ├─ ITEM_TOOLS             道具 ItemCid → (SkillCid, 名称, 类别tag)
  ├─ SKILL_TO_CATEGORY      SkillCid → 类别tag（由上表反推）
  ├─ CATEGORY_NAMES         类别tag → 中文名
  ├─ MAP_SKILL_DESC         地图技能 SkillCid → 描述
  ├─ MAP_SKILL_FORCE_QUALITY 地图技能强制覆写品质的规则
  ├─ SEP / THIN             输出分隔线
  └─ fmt_shape / fmt_categories / fmt_price  格式化工具函数

getlog/models.py
  ├─ CsvItem         item_prices.csv 的一行记录（dataclass）
  ├─ ItemKnowledge   游戏中对某物品的累积已知信息（dataclass）
  └─ GameState       一局游戏的完整状态（class）

getlog/item_db.py
  ├─ load_csv()      解析 CSV，返回 (id索引字典, 全量列表)
  └─ query_item()    按约束查询候选物品，返回 (最佳, 数量, 是否唯一, 均价)

getlog/log_parser.py
  ├─ extract_event() 从单行提取 (事件类型, JSON数据)，失败返回 None
  └─ iter_log_lines()逐行迭代，tail模式持续轮询，EOF时yield None

getlog/processors.py
  ├─ process_hero_skill_log()  处理英雄技能，更新 shape/quality
  ├─ process_map_skill_log()   处理地图技能，提取物品+统计数据
  └─ process_item_skill_log()  处理道具技能，更新类别；支持去重

getlog/renderer.py
  ├─ fmt_item_line()           格式化单行物品信息（含查询结果）
  ├─ calc_total_price()        估算全部物品总价
  ├─ print_all_items_snapshot()输出全量物品快照（含总价）
  ├─ print_events()            批量输出结构化事件
  ├─ print_bids()              输出某回合出价
  └─ print_catchup_summary()   tail追赶完成后输出当前快照

getlog/handlers.py
  ├─ handle_s2c33()  游戏开始：初始化状态，输出第1回合标题
  ├─ handle_s2c37()  回合结算：处理该回合所有事件，输出结算信息
  ├─ handle_s2c39()  实时道具通知：立即输出，按Uid去重
  └─ handle_s2c45()  游戏结束：揭晓所有物品，输出完整清单

getlog/runner.py
  └─ run()  主循环：加载CSV → 迭代日志 → 分发事件 → 管理追赶逻辑
```

---

## 4. 数据流

```
Player.log
    │
    ▼
iter_log_lines()          # 逐行读取，tail模式轮询
    │
    ▼
extract_event()           # 正则提取事件类型 + JSON
    │
    ├─ S2C_33 ──► handle_s2c33()
    ├─ S2C_37 ──► handle_s2c37()
    ├─ S2C_39 ──► handle_s2c39()
    └─ S2C_45 ──► handle_s2c45()
                    │
                    ├─ process_*_skill_log()  # 更新 GameState.items
                    │       └─► ItemKnowledge（逐字段累积）
                    │
                    └─ print_events() / print_bids()
                            │
                            └─► query_item()    # 查 item_prices.csv
                                    └─► fmt_item_line() → stdout / 文件
```

---

## 5. 关键设计决策

### 5.1 物品知识累积（ItemKnowledge）

游戏中每件物品的信息**分散在多个事件中逐步揭露**：

- **英雄技能**：揭示形状（ItemSlotType）和品质上限（ItemQuility）
- **地图技能**：部分揭示形状、品质，或揭示完整信息（200021）
- **道具技能**：揭示物品类别（ItemType）
- **游戏结束**：揭示精确 ItemCid（完整物品 ID）

`ItemKnowledge.update_from_box()` 负责安全合并：只更新日志中存在的字段，不用 `None` 覆盖已有信息。

### 5.2 物品查询优先级（query_item）

```
1. ItemCid 已知 → 精确命中（唯一）
2. 按 shape + quality 过滤 → 再按 categories 过滤 → 取价最高
3. categories 过滤后为空 → 保留 shape+quality 结果（容错）
4. 无任何候选 → 返回 None
```

多候选时：
- **品质 1-4**：显示候选均价 + 最高价候选名称
- **品质 5-6**：仅显示最高价（稀有物品候选少，均价意义不大）

### 5.3 去重机制（displayed_event_uids）

道具使用事件在日志中出现**两次**：
- `S2C_39`：实时推送（使用瞬间）
- `S2C_37`：下回合结算汇总中也包含

`GameState.displayed_event_uids` 记录已处理的事件 `Uid`，
`process_item_skill_log(check_dup=True)` 跳过已记录的条目，防止重复输出。

### 5.4 tail 模式追赶逻辑

启动实时监听时，日志文件中可能已有多局历史记录：

```
启动
 │
 ▼
静默追赶（catching_up=True）
  所有输出重定向到 StringIO（丢弃）
  只更新 GameState 状态
 │
 ▼（读到 EOF）
catching_up = False
 ├─ game_active=True → print_catchup_summary()  输出当前快照
 └─ game_active=False → "等待新对局..."
 │
 ▼
正式监听（输出到 stdout）
  每 0.5s 轮询一次 readline()
```

---

## 6. 常量扩展指南

### 新增英雄技能
在 `getlog/constants.py` 的 `HERO_SKILL_QUALITY` 中添加：
```python
HERO_SKILL_QUALITY: Dict[int, int] = {
    1001031: 4,
    1001032: 3,
    1001033: 2,
    1001034: 1,
    # 新增:
    1001035: 5,   # 假设新技能揭示品质<=5的物品
}
```

### 新增地图技能描述
在 `MAP_SKILL_DESC` 中添加对应 SkillCid 和描述。
若该技能会强制设定物品品质，同时在 `MAP_SKILL_FORCE_QUALITY` 中添加规则。

### 新增道具
在 `ITEM_TOOLS` 中添加：
```python
ITEM_TOOLS: Dict[int, Tuple[int, str, int]] = {
    ...
    100161: (2011, "新道具名称鉴影", 111),  # 新增道具
}
```
`SKILL_TO_CATEGORY` 会自动推导，无需手动维护。

---

## 7. 运行方式

```powershell
# 批量处理（自动查找日志）
uv run python parse_log.py

# 指定日志文件
uv run python parse_log.py --log Player.log

# 实时监听（tail模式）
uv run python parse_log.py --tail

# 输出到文件
uv run python parse_log.py --output result.txt

# 使用已注册的 CLI 命令（需先 uv sync）
uv run parse-log --tail
```

**日志文件查找顺序（未指定 --log 时）：**
1. `./Player.log`（当前目录，便于调试）
2. `C:\Users\Administrator\AppData\LocalLow\laolin\BidKing\Player.log`（游戏实际路径）

---

## 8. 输出格式示例

```
================================================================
  第 1 回合  [游戏开始]
  对局ID: 2105:414722427469816   地图: 2105
  玩家: 栎梧(英雄103)  vs  奥本河狸(英雄204)
================================================================

  [英雄技能 1001034] (品质<=1, 初始扫描)
    BoxId=0    形状:1x1  品质:1                       => [50候选] 均价:¥164  最高: 艾条 ¥331
    BoxId=62   形状:1x2  品质:1                       => [8候选] 均价:¥261  最高: 玩具木剑 ¥346

  ┌─ 当前全部物品 (12 件) ─────────────────────────────────
    BoxId=0    形状:1x1  品质:1                       => [50候选] 均价:¥164  最高: 艾条 ¥331
    BoxId=2    形状:1x3  品质:1  [家具物品]           => [唯一] 竹帘 ¥388
    ...
  └─ 估算总价: ¥12,450 (多候选取均价) ────────────────────────────

  [初始出价]
    栎梧: ¥1,200
    奥本河狸: ¥980
----------------------------------------------------------------
```

---

## 9. 依赖

本项目仅依赖 Python 3.11+ 标准库，无需第三方包：
- `argparse` — CLI 参数解析
- `csv` / `json` — 数据解析
- `re` — 日志行正则匹配
- `time` — tail 模式轮询间隔
- `dataclasses` — 数据模型
- `io` — tail 模式静默缓冲
