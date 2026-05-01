# 艾莎鉴影 · getlog

BidKing 游戏日志解析与 **10×30 物品网格可视化**（tkinter），以及命令行逐回合文本解析。Python 包名为 **getlog**；使用 `BidKingGrid.spec` 打包后的 Windows 程序名为 **艾莎鉴影.exe**。

更完整的模块与数据流说明见 **[ARCHITECTURE.md](ARCHITECTURE.md)**（本 README 后半部分为同内容的存档，便于单文件阅读）。

## 使用说明

### 环境与安装

- 需要 **Python 3.11+**（见 `pyproject.toml`）
- 推荐使用 [uv](https://github.com/astral-sh/uv)：

```powershell
cd <本仓库目录>
uv sync
```

安装含 PyInstaller 的开发依赖（仅打包时需要）：

```powershell
uv sync --group dev
```

### 启动程序

**1）命令行解析日志（终端文本输出）**

```powershell
uv run python parse_log.py
uv run python parse_log.py --log Player.log
uv run python parse_log.py --tail
uv run parse-log
```

未指定 `--log` 时，会依次尝试当前目录的 `Player.log` 与游戏默认路径下的日志（见下文「运行方式」或 `ARCHITECTURE.md` §7）。

**2）网格可视化 UI（`show_grid.py`）**

```powershell
uv run python show_grid.py
```

- **不带参数**：先弹出启动页，选择 `Player.log` 路径，以及 **回放模式**（解析最后一局、可按回合翻页）或 **实时模式**（从文件末尾 tail 监听）。
- **带参数**可直接进入主界面，例如：

```powershell
uv run python show_grid.py --log Player.log
uv run python show_grid.py --log Player.log --tail
```

在候选物品弹窗中：**单击**一行预览；**双击**一行或点「确认所选后选项」可 **手动确认** 该候选，用于网格上的总价估算与品质展示（日志已给出精确 `ItemCid` 时仍以日志为准）。

### 数据文件（与 `item_prices.csv` 同目录）

解析与多候选概率需要 `item_prices.csv`；建议同时保留 `calculator_data_merged.csv`、`drop_table_weights.csv` 及可选的 `物品轮廓爆率推断器.html`。说明见下文 **§2.3** 或 `ARCHITECTURE.md`。

### 打包为 Windows 程序（PyInstaller）

仓库根目录提供 **`BidKingGrid.spec`**：入口为 **`show_grid.py`**，生成 **无控制台窗口** 的单文件 exe，并将下列资源一并打入包内：

- `item_prices.csv`
- `calculator_data_merged.csv`
- `drop_table_weights.csv`
- `物品轮廓爆率推断器.html`

```powershell
uv sync --group dev
uv run pyinstaller BidKingGrid.spec
```

产物路径：`dist\艾莎鉴影.exe`。若要修改成品名称，编辑 spec 中 `EXE(..., name='艾莎鉴影')`。

> **说明**：当前 spec **未**打包 `parse_log.py`。命令行工具请直接运行源码或使用 `uv run parse-log`。若需要单独的 CLI 可执行文件，需另写 `.spec` 并设置 `console=True`。

---

> 以下面向开发者与二次维护：项目目标、输入文件、模块结构、扩展方式等。

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

### 2.3 地图掉落权重（可选，但强烈建议保留）

多候选物品的**期望价**与**出现概率**会读取与 `item_prices.csv` 同目录下的权重数据。`getlog/item_db.py` 中 `load_csv()` 在加载物品表后会自动调用 `load_weight_data(base_dir)`。

| 文件 | 作用 |
|---|---|
| `calculator_data_merged.csv` | **优先使用**。含 `record_type=ITEM` 与 `record_type=DROP` 的合并表；`DROP` 行构成完整掉落有向图（`drop_id` → `ref_id`，`ref_id` 可为下级 `drop_id` 或物品 `item_id`），可表达地图专属根池与多层嵌套。 |
| `drop_table_weights.csv` | **后备**。仅四列 `drop_id,ref_id,weight,ref_type` 的简化边表；语义与合并表中的 `DROP` 行一致。 |
| `物品轮廓爆率推断器.html` | **可选**。若存在，会解析其中的 `NEST_W`、`SUBMAP_PRIOR_MULT`（子图品质巢与子图池化倍率）；缺失时相关倍率视为 1。 |

**与游戏表的关系：** 游戏内 `Drop.txt`（Tab 分隔、表体常为 Base64）描述掉落池 ID 与权重链；本仓库的 CSV 是将其中与概率相关的边导出为上述格式，供解析器离线递归使用。版本更新后若出现新地图或新 `drop_id`，需要同步更新 CSV（及下文 `MAP_TO_TIER_NEST`），否则多候选会回退到均匀分布或全局权重。

---

## 3. 模块结构

```
e:\game\getLog\
├── parse_log.py          # CLI 入口（薄包装）
├── show_grid.py          # 物品网格 UI（无参数时弹出启动页）
├── pyproject.toml        # 项目配置（uv/pip）
├── item_prices.csv       # 物品静态数据库
├── calculator_data_merged.csv   # 物品 + DROP 合并权重（优先）
├── drop_table_weights.csv       # 简化 DROP 边表（后备）
├── 物品轮廓爆率推断器.html      # 可选：子图巢权重与池化倍率（HTML 内嵌常量）
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
    ├── grid_view.py      # 可选：网格视图等 UI（若使用则依赖 item_db 的地图权重）
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
  ├─ load_csv()                 解析 item_prices.csv；同目录加载权重与 HTML 先验
  ├─ load_weight_data()         优先 merged CSV，否则 drop_table_weights.csv
  ├─ load_drop_weights()        解析 DROP 行/简化表，构建 _DROP_GRAPH 与按物品的叶子权重索引
  ├─ load_map_prior_data()      可选：从 HTML 解析 NEST_W、SUBMAP_PRIOR_MULT
  ├─ normalize_map_id()         日志 MapId 归一（如 41xx~45xx → 21xx~25xx）
  ├─ MAP_TO_TIER_NEST           地图 ID → (档位 tier, 地图根 drop_id)
  ├─ TIER_REF_NEST              各 tier 用于对比的参考巢 drop_id（品质巢倍率）
  ├─ _resolve_drop_to_items()   从指定 drop_id 递归展开到物品，在候选集合内归一化份额
  ├─ candidate_probabilities()  多候选时归一化出现概率（可传 map_id）
  ├─ probability_source_label() 标注当前用的是地图递归权重还是全局回退
  ├─ map_category_ratios()      从地图根 drop 推导类别占比（含经通用品质池直达物品的地图）
  └─ query_item()               按约束查询候选物品，返回 (最佳, 数量, 是否唯一, 估算价, 说明)

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
                            └─► query_item()    # 查 item_prices.csv + 可选掉落权重（§2.3）
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

估算均价时：若同目录存在有效掉落图（见 **§2.3**），会按当前地图根池递归权重对候选做加权平均（`_weighted_est_price()`）；否则退化为简单统计。

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

### 5.5 地图掉落权重与多候选概率

当 `query_item()` 在**多候选**下估算价格时，会结合当前地图 `MapId`（经 `normalize_map_id()` 归一）在 `MAP_TO_TIER_NEST` 中查到**地图根掉落池** `drop_id`，再用 `_resolve_drop_to_items()` 在整张 `_DROP_GRAPH` 上按边权重做 DFS：若 `ref_id` 是已知物品（存在于 `item_prices.csv` 加载后的索引），则把路径上各段权重相乘后累计到该物品；若 `ref_id` 仍是图中的 `drop_id`，则继续向下展开。最终在**当前候选物品集合**上归一化，得到每个候选的相对概率，用于加权期望价。

**要点：**

- **有 `calculator_data_merged.csv` 时**：使用其中的 `record_type=DROP` 行构建完整图；地图根池可包含专属链（如新地图 `2601 → 2051 → …`）。
- **仅有 `drop_table_weights.csv` 时**：图为简化边表；若某地图根池未覆盖当前候选，会回退到全局/旧逻辑（见 `query_item` / `candidate_probabilities` 内注释）。
- **`drop_id` 与类别/品质编码**：解析时会把 `drop_id` 拆成 `category = drop_id // 10`、`quality = drop_id % 10`，用于叶子权重索引；与游戏内「类别×10+品质」类池 ID 一致。
- **经通用品质池的地图**（如排位地图 `2601`）：中间层可能是 `1201~1206` 等池，**直接连到具体物品**，不再经过 `1011~1106` 这种「类别+品质」中间节点。此时 `map_category_ratios()` 会按物品的 `category_tags` 把份额分摊到各类别（多 tag 物品按 tag 数均分），保证状态栏「类别 TOP」仍有意义。
- **子图倍率**：若存在 `物品轮廓爆率推断器.html`，`_nest_quality_multiplier()`、`_submap_pool_multiplier()` 会在**未使用**「地图根已解析到物品份额」路径时参与 `_candidate_weight()`；当已使用完整地图 `map_drop_weights` 时，避免与叶子池重复叠乘。

**日志地图 ID 归一：** 部分日志使用 `4301` 等与权重表 `2301` 相差 2000 的写法，`normalize_map_id()` 会把 `map_id - 2000` 再尝试匹配 `MAP_TO_TIER_NEST`。

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

### 游戏版本更新后：新地图 / 新掉落池

1. **解码游戏表**（若仓库外有 `decode_tables.py`）：对 `StreamingAssets/Tables` 下 Base64 的 `*.txt` 解码得到明文 `Drop.txt`、`RankMap.txt` 等。
2. **从 `Drop.txt` 提取新边**：将涉及新地图根 `drop_id` 及其子图上的所有 `[[ref_type, ref_id, …, weight], …]` 转为 CSV：
   - `calculator_data_merged.csv`：`record_type=DROP`，列与现有文件一致（`drop_id, ref_id, weight, ref_type` 等）；
   - 或至少更新 `drop_table_weights.csv` 四列。
3. **登记地图根池**：在 `getlog/item_db.py` 的 `MAP_TO_TIER_NEST` 中增加 `地图 MapId → (tier, root_drop_id)`；若新档位需要参考巢，在 `TIER_REF_NEST` 中增加对应 `tier → ref_drop_id`。
4. **校验**：`item_prices.csv` 须包含图中所有作为叶子出现的 `item_id`；运行 `python -m py_compile getlog/item_db.py` 与一次 `load_csv('item_prices.csv')` 后检查 `probability_source_label` 是否为「地图权重 …」而非全局回退。

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

### 7.1 网格可视化（`show_grid.py` / `getlog/grid_view.py`）

```powershell
uv run python show_grid.py                 # 无参数：弹出启动页，选日志与回放/实时后点「启动」
uv run python show_grid.py --log Player.log
uv run python show_grid.py --log Player.log --tail   # 跳过启动页，直接实时监听
```

**候选弹窗 — 手动确认物品：** 左键点击格子上某物品后弹出候选表。在表中**单击**一行可预览；**双击**该行，或点击「确认所选后选项」，将把该 `item_id` 写入 `ItemKnowledge.manual_confirm_item_id`，用于该格的**总价估算**与**品质显示**（与日志已揭示的精确 `ItemCid` 并存时仍以日志为准）。「取消确认」可清除。主窗口图例栏亦有简短提示。

---

## 8. 输出格式示例

```
================================================================
  第 1 回合  [游戏开始]
  对局ID: 2105:   地图: 2105
  玩家: xxx(英雄103)  vs  xxx(英雄204)
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
