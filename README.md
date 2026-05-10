# 艾莎鉴影 · getlog

BidKing 游戏日志解析与 **10×30 物品网格可视化**（tkinter），以及命令行逐回合文本解析。Python 包名为 **getlog**；使用 `BidKingGrid.spec` 可打包为 Windows 程序 **艾莎鉴影.exe**。

感谢以下开源项目与对应 B 站视频提供的思路参考：

- [yuewusan/bidking](https://gitee.com/yuewusan/bidking)，BV1PeRMBeEFZ
- [Jrinky908/bidking](https://github.com/Jrinky908/bidking.git)，BV18hRvBnE1X

前半为**使用说明**，后半为**实现与扩展说明**（原独立架构文档已并入本 README，单处维护即可）。

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

未指定 `--log` 时，`parse_log.py` 会读取游戏默认日志路径：

`C:\Users\Administrator\AppData\LocalLow\laolin\BidKing\Player.log`（逻辑见 `getlog/constants.py` 的 `default_game_log_path()`）

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

Grid 窗口信息栏下方有「置顶」开关，可让窗口保持在其它窗口上方。在候选物品弹窗中：**单击**一行预览；**双击**一行或点「确认所选后选项」可 **手动确认** 该候选，用于网格上的总价估算与品质展示（日志已给出精确 `ItemCid` 时仍以日志为准）。

输入区支持录入 **金个数 / 金总格 / 金均格** 后点「确认」刷新估算；「重置」会清空这些输入约束并重新按当前 grid 状态估算。「尝试填充」会在紫色品质显示完之后，把当前可推断为空的区域按 Q5/Q6 候选形状自动补成手动画框，并枚举、去重多个填充方案；方案排序会优先尝试大块、再尝试小块，反复点击会按方案顺序切换。顶部状态栏会显示各品质件数、格数、均格，并在「已知金」后显示 **金红总共格数**。

启动页的「导出全部记录」只会新增导出尚未写入 `records\manifest.json` 的**已结束**对局；已经导出过的对局会跳过，日志末尾仍在进行中的“未结束”对局不会导出。

记录回放模式读取 `records\*.json`，可直接打开已导出的历史对局并按回合翻页。导出时的 `manifest.json` 负责记录已导出的 `game_uid`，因此重复点击「导出全部记录」通常只会处理新增结束的对局。

### 数据文件（与 `item_prices.csv` 同目录）

解析与多候选概率需要 `item_prices.csv`；建议同时保留 `calculator_data_merged.csv`、`drop_table_weights.csv` 与可选的 `物品轮廓爆率推断器.html`。说明见下文 **§2.3**。

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

## 1. 项目目标

从 `Player.log` 提取对局事件，结合 `item_prices.csv` 推断每件物品的可能名称与价格；可选掉落权重（§2.3）用于多候选加权。主要能力：逐回合事件与出价、技能揭示累积、候选查询与全场总价估算（与上文「使用说明」一致）。

### 1.1 游戏规则概述

按当前项目整理，这个游戏的核心规则可以概括为：

- 4 个玩家参与同一局拍卖
- 每个玩家有角色；本项目当前对应的角色技能规则是按回合揭示品质：
  - 第 1 回合显示白色物品
  - 第 2 回合显示绿色物品
  - 第 3 回合显示蓝色物品
  - 第 4 回合显示紫色物品
  - 第 5 回合起不再新增这类角色揭示
- 对局按回合推进，每回合所有玩家都会提交竞拍价格
- 若本回合第一名出价达到第二名的指定倍数，则视为直接拍下
- 该倍数门槛会随回合下降，当前整理到的示例为：
  - 第 1 轮：2.0 倍
  - 第 2 轮：1.6 倍
  - 第 3 轮：1.3 倍
  - 第 4 轮：1.1 倍
  - 第 5 轮：1.0 倍
- 拍下后，以仓库物品总价值作为参考，对比成交价判断是赚还是亏

本仓库的主要目标，是根据日志逐回合揭示的信息，尽量还原仓库内物品的布局、候选、总价与总格数，用于辅助判断当前竞拍价格是否合理。

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
├── read_last_round.py    # 读取最近一局/最近回合记录的辅助脚本
├── pyproject.toml        # 项目配置（uv/pip）
├── item_prices.csv       # 物品静态数据库
├── calculator_data_merged.csv   # 物品 + DROP 合并权重（优先）
├── drop_table_weights.csv       # 简化 DROP 边表（后备）
├── 物品轮廓爆率推断器.html      # 可选：子图巢权重与池化倍率（HTML 内嵌常量）
├── records\              # 导出的已结束对局回放记录与 manifest
├── README.md             # 使用说明 + 实现说明（本文件）
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
    ├── round_recorder.py # 导出/读取 records 回放记录
    ├── posterior_estimator.py # 候选权重值与价格软约束工具
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

getlog/round_recorder.py
  ├─ export_round_records_to_directory() 增量导出已结束对局到 records/*.json
  ├─ load_round_record_game_for_grid()   读取回放记录供 Grid 翻回合
  └─ read_last_round_record()            读取最近一局最近回合信息

getlog/processors.py
  ├─ process_hero_skill_log()  处理英雄技能，更新 shape/quality
  ├─ process_map_skill_log()   处理地图技能，提取物品+统计数据
  └─ process_item_skill_log()  处理道具技能，更新类别；支持去重

getlog/renderer.py
  ├─ fmt_item_line()           格式化单行物品信息（含查询结果）
  ├─ calc_total_price()        按候选价值期望估算总价格
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
  ├─ run()                       CLI 主循环：加载CSV → 迭代日志 → 分发事件
  ├─ parse_last_game()           从日志尾部倒找最后一局并解析
  └─ parse_last_game_state_from_tail()  给实时 UI 后台恢复状态使用
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

### 5.4 Grid 实时模式追赶逻辑

启动实时监听时，日志文件中可能已有多局历史记录。Grid UI 会先打开窗口并从日志末尾开始监听新增行，同时后台从文件尾部反向寻找最近一次 `S2C_33_game_start_notify`，只解析最后一局用于恢复当前状态：

```
启动
 │
 ▼
打开 Grid 窗口
  后台线程 A：从启动时 EOF 继续 tail 新增日志
  后台线程 B：从文件尾部倒找最后一局并恢复 GameState
 │
 ▼
恢复完成
  先安装最后一局状态
  再按顺序回放启动期间缓存的新日志事件
 │
 ▼
持续监听
  每 0.3s 轮询一次 readline()
```

这样实时模式不需要在开窗前从头扫描整个 `Player.log`。若最后一局本身非常大，后台恢复仍可能需要一些时间，但不会阻塞启动页和主窗口显示。

### 5.5 地图掉落权重与多候选概率

当 `query_item()` 在**多候选**下估算价格时，会结合当前地图 `MapId`（经 `normalize_map_id()` 归一）在 `MAP_TO_TIER_NEST` 中查到**地图根掉落池** `drop_id`，再用 `_resolve_drop_to_items()` 在整张 `_DROP_GRAPH` 上按边权重做 DFS：若 `ref_id` 是已知物品（存在于 `item_prices.csv` 加载后的索引），则把路径上各段权重相乘后累计到该物品；若 `ref_id` 仍是图中的 `drop_id`，则继续向下展开。最终在**当前候选物品集合**上归一化，得到每个候选的相对概率，用于加权期望价。

**要点：**

- **有 `calculator_data_merged.csv` 时**：使用其中的 `record_type=DROP` 行构建完整图；地图根池可包含专属链（如新地图 `2601 → 2051 → …`）。
- **仅有 `drop_table_weights.csv` 时**：图为简化边表；若某地图根池未覆盖当前候选，会回退到全局/旧逻辑（见 `query_item` / `candidate_probabilities` 内注释）。
- **`drop_id` 与类别/品质编码**：解析时会把 `drop_id` 拆成 `category = drop_id // 10`、`quality = drop_id % 10`，用于叶子权重索引；与游戏内「类别×10+品质」类池 ID 一致。
- **经通用品质池的地图**（如排位地图 `2601`）：中间层可能是 `1201~1206` 等池，**直接连到具体物品**，不再经过 `1011~1106` 这种「类别+品质」中间节点。此时 `map_category_ratios()` 会按物品的 `category_tags` 把份额分摊到各类别（多 tag 物品按 tag 数均分），保证状态栏「类别 TOP」仍有意义。
- **子图倍率**：若存在 `物品轮廓爆率推断器.html`，`_nest_quality_multiplier()`、`_submap_pool_multiplier()` 会在**未使用**「地图根已解析到物品份额」路径时参与 `_candidate_weight()`；当已使用完整地图 `map_drop_weights` 时，避免与叶子池重复叠乘。

**日志地图 ID 归一：** 部分日志使用 `4301` 等与权重表 `2301` 相差 2000 的写法，`normalize_map_id()` 会把 `map_id - 2000` 再尝试匹配 `MAP_TO_TIER_NEST`。

### 5.6 估算总价格

> **注意：估算并非一定准确，仅供参考。** 估算依赖当前日志已揭示信息、手动标记、掉落权重和物品数据库；当候选池很大、约束不足、地图权重不完整或手动输入有误时，结果可能明显偏离真实价值。

Grid 底部显示 **估算总价格**，估价按钮左侧可以选择估价方法。

- **`1.候选价值期望`**：对每个物品先生成“它可能是哪几个物品”的候选池，再给每个候选一个概率权重，计算该物品的加权平均价值，最后把所有物品的期望价值相加。
- **`2.方差校正期望`**：同样先计算每个物品的候选期望，但会额外计算候选价格方差。总价先取 `总期望`，再扣掉一部分 `总标准差` 作为不确定性惩罚，当前公式为 `总期望 - min(总期望 × 30%, 总标准差 × 25%)`。它不会改变候选概率，只是让高波动、高长尾的局面更保守，通常能缓解估价被少数高价候选抬高的问题。
- **`3.截尾均值估价`**：对每个物品的候选按价格从低到高排序，去掉最高价端 5% 的概率质量，再用剩余 95% 概率质量计算均值。它比方差校正更直接地压低长尾高价候选的影响。
- **`4.85%期望估价`**：先按 `1.候选价值期望` 算出总期望，再直接乘以 85%。这是最简单的保守折扣法，适合只想快速压低整体估值的情况。

手动在 grid 上画出的“有物品”默认视作 **Q5/Q6 高品质范围**。未指定金/红时，它会直接在该形状对应的 Q5/Q6 候选池上估算。若随后手动设为金/红或确认候选，估算总价格会按明确约束刷新。

输入区的 **金个数** 用于记录道具得到的“整局金色 Q5 物品件数”。点击「确认」后，程序会用这个数辅助分配未定的 Q5/Q6 物品；顶部「已知金」会优先显示用户输入或由输入推导出的金件数、金总格、金均格：

- grid 上已经确定为金的物品会先占用金件数名额。
- grid 上已经确定为红的物品不会占用金件数名额。
- 仍未指定金/红的手动画框，会在 Q5/Q6 之间分配，直到总金件数等于输入值。
- 如果当前 grid 已知金数已经超过输入值，或未定 Q5/Q6 数量不足以补到输入值，则认为约束矛盾，估算总价格会显示原因提示。
- 点击「重置」会清空输入区所有金色约束，并让估算恢复为只依赖当前日志、手动标记和候选池。
- 点击「尝试填充」会清除上一次自动填充生成的框，保留你手动画的框，然后从第 3 回合开始把仍为空的区域按 Q5/Q6 候选物品形状尝试铺满。程序会用左上、右下、按列、中心、边缘等不同扫描方向生成方案并去重；方案选择优先保留彼此差异更大的全局填法，再按块数少、大块多、候选权重排序；只反复点击「尝试填充」会按方案顺序切换。为避免点击时卡顿，自动填充不会逐方案计算估价，也不会弹出填充估价表。若已输入金个数/金总格/金均格，程序会尽量把自动填充框标成金或红来满足约束；若没有足够约束，则保持金/红未定。

输入区的 **金总格** 和 **金均格** 也会参与同一套约束：

- **金总格**：约束最终所有 Q5 物品的总占格数。
- **金均格**：约束 `金总格 / 金个数`。输入框支持小数，最多两位。
- 如果只输入 `金均格`，程序会枚举可能的 `(金个数, 金总格)` 组合，保留那些让 `金总格 / 金个数` 接近输入均格的组合。
- 如果输入了 `金个数 + 金均格`，程序会反推可能的 `金总格`。
- 如果输入了 `金总格 + 金均格`，程序会反推可能的 `金个数`。
- 如果三个都输入了，三者必须互相匹配，否则视为约束矛盾。

均格匹配使用近似判断：若 `abs(金总格 / 金个数 - 金均格) <= 0.02`，就认为这个组合可用。这个容差用于兼容游戏 UI 的两位小数显示。

顶部状态栏还会显示 `金红总共格数`，即当前已知 Q5 金色格数、Q6 红色格数，以及手动画出但金/红未定的 Q5/Q6 高品质框格数之和，方便和游戏内道具给出的高品质总格信息对照。

#### 5.6.1 单个物品候选池

估算总价格目前不是完整的全局二维重排搜索；它不会枚举所有可能布局。它使用的是当前 grid 已经确定的物品形状、位置、手动画框和候选过滤结果。

输入来源：

- 当前地图 `map_id`
- 物品形状、品质、类别、排除类别、排除品质
- 已知 `ItemCid` 或手动确认的候选
- 已知价格 `ItemPrice`
- 手动画框尺寸
- `calculator_data_merged.csv` 或 `drop_table_weights.csv` 中的掉落图
- 可选的 `物品轮廓爆率推断器.html` 中的地图先验倍率

单个物品的候选池生成步骤：

1. 如果日志已经给出 `ItemCid + ItemPrice`，这个物品直接视为确定值，分布只有一个点：

```text
{已知价格: 100%}
```

2. 如果用户在候选弹窗里手动确认了候选，也视为确定物品：

```text
{该物品 base_value: 100%}
```

3. 否则根据约束过滤 `item_prices.csv`：
   - 形状必须匹配，手动画框会转成对应形状，例如 `2x3 -> 23`
   - 品质必须匹配
   - 手动画框未设金/红时，只允许 Q5/Q6
   - 类别必须满足已知 `ItemType`
   - 排除类别、排除品质必须避开
   - 如果 BoxId 可靠，会用当前占位情况过滤放不下的形状

4. 对候选池里的每个物品计算基础概率。
   这个概率来自 `candidate_probabilities()`：
   - 优先从当前地图的掉落根池递归展开到具体物品
   - 如果地图根池覆盖不了当前候选集合，回退到全局/旧权重
   - 最后在当前候选池内归一化

5. 如果这个物品有已知价格但没有精确 `ItemCid`，会把价格作为软约束。
   候选价格越接近已知价格，权重越高；偏离越大，权重越低。这样做是为了避免 OCR 或日志不完整时把候选权重直接压成 0。

#### 5.6.2 总价合成

单个物品最后得到的是类似这样的分布：

```text
候选A: 价格 35,000，概率 48%
候选B: 价格 72,000，概率 31%
候选C: 价格 11,000，概率 21%
```

总价合成步骤：

1. 把每个物品的候选价格分布收集起来。

2. 对每个物品单独计算加权平均价值：

```text
单物品期望价 = Σ(候选价格 × 候选权重) / Σ候选权重
```

3. 把所有物品的期望价相加：

```text
估算总价格 = Σ单物品期望价
```

这样做的目的：

- 结果更稳定，同一局面不会因为抽样产生跳动。
- 计算更快，适合拖动、填充、手动标记后的频繁刷新。
- 更容易解释：每个物品值多少钱，直接由它的候选价格和候选概率决定。

选择 **`2.方差校正期望`** 时，会在上述结果基础上估计总方差：

```text
单物品方差 = Σ((候选价格 - 单物品期望价)^2 × 候选权重) / Σ候选权重
总标准差 = sqrt(Σ单物品方差)
校正估价 = 总期望 - min(总期望 × 30%, 总标准差 × 25%)
```

这个方法仍然不是完整全局约束推断；它只是用方差识别“不确定性很高”的局面，并给出更保守的估价。

选择 **`3.截尾均值估价`** 时，会对每个物品单独做高价端截尾：

```text
按候选价格从低到高排序
保留低价侧 95% 概率质量
截尾均值 = Σ(保留部分候选价格 × 保留权重) / Σ保留权重
```

选择 **`4.85%期望估价`** 时，计算最简单：

```text
85%期望估价 = 候选价值期望 × 0.85
```

这两个方法同样不会改变候选池，也不会做完整布局枚举；它们只是给当前候选期望增加不同程度的保守修正。

如果输入了 **金个数 / 金总格 / 金均格**，这些信息主要用于两件事：

- 在「尝试填充」和「二次填充」后，把自动填充出来的 Q5/Q6 高品质框尽量分配成金或红。
- 检查明显不可能满足的金色约束；如果约束矛盾，估算栏会显示原因提示。

当前版本不会再把总价分布按金色约束做 Monte Carlo 条件采样，也不会使用 `static_q56` 静态总价表。也就是说，估价总价格优先反映“当前 grid 上每个物品候选池的价值期望”；如果你希望金个数强烈影响估价，需要先通过手动标记、尝试填充或二次填充把对应物品明确成金/红。

例子：如果两个未定 Q5/Q6 手动画框分别是 `4` 格和 `2` 格：

- 输入 `金均格 = 4.00`，可能推导出 `(金个数=1, 金总格=4)`，也就是 4 格框为金。
- 输入 `金均格 = 2.00`，可能推导出 `(1, 2)` 或 `(2, 4)`，程序会优先用这些合法分支辅助分配金/红。
- 输入 `金个数 = 1` 且 `金均格 = 3.67`，没有任何整数总格能匹配，估算栏会提示金约束不匹配。


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

## 7. 输出格式示例

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
  └─ 估算总价格: ¥13,120 （估算并非一定准确，仅供参考）──────────────

  [初始出价]
----------------------------------------------------------------
```

---

## 8. 依赖

运行时仅依赖 **Python 3.11+** 标准库，无需第三方包：
- `argparse` — CLI 参数解析
- `csv` / `json` — 数据解析
- `re` — 日志行正则匹配
- `time` — tail 模式轮询间隔
- `dataclasses` — 数据模型
- `io` — tail 模式静默缓冲

打包 Windows exe 时通过 `uv sync --group dev` 安装 **PyInstaller**（见上文「使用说明」），不属于运行时依赖。

---

## 9. 赞助

如果你喜欢本项目，欢迎随缘赞助 **3 元、5 元** 意思一下，帮我回一点维护文档和开发时消耗的 **AI Token** 成本。不强求，用得开心就好。

**支付宝**（扫码或保存图片到相册）：

![支付宝赞助](赞助.jpg)

