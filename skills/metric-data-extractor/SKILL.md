---
name: metric-data-extractor
description: 华为广告数据查询工具。当用户需要查询具体的广告指标数据时使用，包括：查询点击量、曝光量、消耗、转化数等指标；按时间、渠道、产品等维度拆解数据；搜索推广标的、媒体等维度值；对比不同产品或渠道的投放效果。支持自然语言查询，自动处理指标映射和维度搜索。适用场景：数据查询、指标提取、维度搜索、效果对比。不适用于：问题诊断分析、数据计算（占比、同比等）、报告生成、策略建议。
metadata: {}
user-invocable: true
---

# 核心指标数据提取

数据分析的"双手"：统一的指标查询接口，自动处理实体对齐、语义检索、API 认证和错误纠正。

## 硬约束（违反即失败）

1. **禁止自创指标 / 公式**：用户问"ROI"等不在系统的指标，**先问用户"用哪个公式"**，不要自己定义。
2. **必须英文 code**：`metrics` / `dimensions` / `filters` 的 key 都用英文 code（如 `click` 不是"点击量"，`promotionTarget` 不是"推广标的"）。不知道有哪些 code 时调 `list-metrics` / `list-dimensions`。
3. **商业广告必须过滤**：用户问"流水 / 点击 / CPM / CVR" 等用于经营分析的指标时，必须加 `--filters "business_partnership=in:商业合作,集团商业竞价结算"`，否则会包含尾量 / 非商业数据。`business_partnership` 5 个值里只有这两个算商业流水（其余三个 `华为集团 / 自运营 / 集团商业竞价不结算` 都不算）。
4. **`day` 与 `reqDay` 不能混用**：
   - 事件时间 (`event`) → 用 `day`（虚拟标记，自动转 `timingDimension`）
   - 请求时间 (`request`) → 用 `reqDay`（真实 API 维度，系统自动注入）
5. **避免无过滤宽查询**：工具自动截断到 1000 条；返回里有 `truncated: true` 说明数据不全，**不能据此给全量结论**。
6. **`--filters` 支持维度和指标过滤**：过滤条件 source 可以是维度 code（如 `mediaName`、`promotionTarget`）也可以是指标 code（如 `cost`、`exposure`）。系统会自动路由到后端对应的 `dimensionCondition` 或 `indicatorCondition`。**用 `list-dimensions` 查看可用维度，`list-metrics` 查看可用指标**。
7. **mediaName 双值兼容（数据脏，临时绕过）**：现实数据里"华为浏览器"以两个不同标签同时存在 —— `华为浏览器` 和 `HUAWEI Browser`。任何涉及华为浏览器的查询**必须用 IN 同时包两值**，否则会漏数：
   ```bash
   ✅ --filters "mediaName=in:华为浏览器,HUAWEI Browser"
   ❌ --filters "mediaName=华为浏览器"   # 只查到一半
   ```
   涉及多媒体对比时把这两值视作同一行汇总（求和后再展示），不要让用户看到"华为浏览器 X 元、HUAWEI Browser Y 元"两行——业务上是同一媒体。
   （数据团队后续会归一化清洗，届时此约束可移除。）

## 工作流程

```
1. 确认时间口径 (event / request)
2. 维度值不确定 → search-dimension-values 找准
3. query-metrics 执行查询
4. 检查返回的 truncated 字段；如 true 缩窄条件或分批
```

需要了解系统能力时调 `list-metrics` / `list-dimensions`，**不要把对照表死记在 prompt 里**。

## 时间口径选择

判断标准：用户关心的是「事件何时发生」还是「广告何时投放带来的归因」。

| 用户问的关键词 | 选 |
|---|---|
| 曝光、点击、展现、实际发生 | `event` |
| 转化、归因、投放效果、带来、CPA、ROI、转化率 | `request` |

**转化类指标（CVR / CPA / 转化数）必须用 `request`**。
默认场景用 `event`。

## 日期换算（系统会自动告诉你今天）

工具默认 `end-date = yesterday`（当天数据未同步），`start-date = yesterday - (N-1)`。

- 最近 N 天 → `start = today - N`，`end = today - 1`
- 上周 → 上周一到上周日
- 上月 → 上月 1 日到月末

## 区分计费方式 vs 采买模式

| 维度 | 用于 |
|---|---|
| `priceType`（计费方式：CPC / CPM / CPA / oCPC 等） | 看不同**出价类型**的数据 |
| `promotionType`（采买模式：竞价 / 合约 / 定价 / 分成） | 看**竞价 vs 合约**的对比 |

**注意**：`priceType=CPM` 不等于"合约广告"——区分竞价 / 合约必须用 `promotionType`。

## 推广侧 vs 媒体侧（容易混淆的维度组）

广告业务里有两组容易混的维度：「广告主在推什么」（推广侧）vs「广告展示在哪里」（媒体侧）。
选错维度直接拿不到对的数据。

| 想分析什么 | 用哪个维度 |
|---|---|
| 广告主推什么业务（车型、保险产品、电商 APP） | `promotionTarget` |
| 广告主推的 APP 名 / 包名（推广对象是 APP 时） | `promoteAppName` / `promoteAppPkg` |
| 广告显示在哪个 APP（媒体侧） | `mediaAppName` / `packageName` |
| 广告显示在哪个媒体品牌（如华为浏览器） | `mediaName` |
| 广告主自己属于哪个行业（投广告的人） | `operatingIndustryLevel1NotConsistent` / `Level2` |
| 广告投放在哪个行业的媒体（被投放的媒体行业） | `appFirstIndustryClass` / `appSecondIndustryClass` |

**典型错例**：用户问「问界 APP 在哪些媒体投放最多」——
- ❌ 用 `promoteAppName=问界汽车 --dimensions mediaAppName` 但传错维度成 `promoteAppName`
- ✅ `--filters "promoteAppName=问界汽车" --dimensions "mediaName"`（**过滤推广侧 + 拆媒体侧**）

## dimension_type 字段语义（list-dimensions 输出的"类型"列）

每个维度的 type 决定查询习惯：

| type | 含义 | 查询惯用法 |
|---|---|---|
| `enum` | 值固定枚举（如 priceType / adGroupStatus） | 直接传值，不用先 search |
| `semi-enum` | 常见值有限但可扩展（如 mediaName / 行业类） | 不确定写法时先 `search-dimension-values`；常见值可直接传 |
| `string` | 自由文本（任务名 / 广告主名 / 推广标的） | 不确定具体值时**必须** search 找精确值；模式匹配用 `like:` / `notlike:` |
| `id` | 标识符（adGroupId / corpId 等） | 直接传精确值；分析建议改用对应 name 维度更可读 |
| `package` | 包名格式（com.xxx.xxx） | 同 string，可 `like:` 模式过滤 |
| `date` | 日期（day / reqDay） | `YYYY-MM-DD` 格式，搭配 `--time-mode` |

## 漏斗类指标对比（容易选错）

广告漏斗里"请求 → 返回 → 曝光 → 点击 → 转化"每段都有对应的指标，名字相似但口径不同。

### 请求 / 返回类（次数 vs 个数）

一次广告请求可包含多个广告 → "次数"是**请求事件数**，"个数"是**广告条数**。

| 指标 | 含义 | 口径 |
|---|---|---|
| `adsRequestCount` / `adRequestPV` | 广告请求事件数 | 次数 |
| `adRequestNumber` | 广告请求的广告条数 | 个数 |
| `adRequestUV` | 去重用户数 | UV |
| `adReturnCount` | 媒体返回事件数 | 次数 |
| `mediaReceiveCount` | 媒体返回的广告条数 | 个数 |

**所有「请求 / 返回」类指标都不支持 corpName / corpId 维度**——广告请求和广告主无关。
list-metrics 输出的"不支持维度"列已标注；强行用会拿到错误数据。

### 填充率 / 展示率（分母不同）

| 指标 | 公式 | 用途 |
|---|---|---|
| `adFillRateCount` 填充率（次数） | 返回次数 / 请求次数 | 看请求级填充 |
| `adFillRateNumber` 填充率（个数） | 返回个数 / 请求个数 | 看广告级填充 |
| `adImpRateCount` 展示率（次数） | 应收曝光 / 返回次数 | 看返回 → 曝光的事件级转化 |
| `adImpRateNumber` 展示率（个数） | 应收曝光 / 返回个数 | 看返回 → 曝光的广告级转化 |

不确定该选次数还是个数：**默认用次数版**（`Count` 后缀），事件级口径最常见。

### 转化类必须 request 时间口径

`cvr` / `realityConversionCost` / `adGroupShallowConversionNumber` 等转化指标**必须**配
`--time-mode request`——转化是从请求开始归因的，用 event 口径会算错。

### 浅层 / 深层目标（双出价场景）

广告主任务可设单出价（仅一个转化目标）或双出价（浅层 + 深层两个目标，如「下载 + 付费」）。
**所有"浅层"系列指标都有对应的"深层"版本**——双出价任务必须两个都看才完整：

| 浅层（默认） | 深层（仅双出价任务有值） |
|---|---|
| `cvr` 转化率（默认浅层） | `deepCvr` 深层 CVR |
| `adGroupShallowConversionNumber` 浅层转化数 | `adGroupDeepConversionNumber` 深层转化数 |
| `expectedBid` 期望出价 / 浅层目标出价 | `deepExceptConversionCost` 深层目标出价 |
| `shallowCostOffset` 浅层成本抵进 | `deepCostOffset` 深层成本抵进 |
| `realityConversionCost` 实际转化成本（默认浅层） | （后台暂未提供深层实际成本指标） |
| —— | `deepCtcvr` 深层端到端转化率（曝光→深层转化） |

**判断双 vs 单出价**：用 `shallowEffectType` / `deepEffectType` 维度——deepEffectType 非空且非
默认值的任务即双出价。单出价任务的"深层"指标值通常为 0 或空。

## 维度组合粒度警示

工具单次返回上限 1000 行，**高 cardinality 维度组合**会触发截断（结果不完整）。常见会爆的组合：

- `slotId × day` / `slotName × day`（广告位 × 日历）—— slot 数百到数千
- `adGroupId × day` 或 `adGroupName × day` —— 任务往往上千
- `campaignId × day` —— 计划数也大
- 任何带 `corpId` / `corpName` 的细维度组合

**应对**：加 `--page-size 1000 --sort-by <核心指标> --sort-order desc`，配合**显式过滤**（如限定 corpName / 限定 mediaName）缩范围。看到返回里 `truncated: true` 必须缩窄条件重查。

---

## 工具

### query-metrics

```bash
query-metrics \
  --metrics "click,exposure,cost" \
  --start-date "2026-01-01" --end-date "2026-01-15" \
  --time-mode event \
  --dimensions "day,promotionTarget" \
  --filters "promotionTarget=问界M7;business_partnership=商业合作"
```

**Windows PowerShell**：单行命令，避免续行符。

#### 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `--metrics` | ✅ | 英文 code，逗号分隔（如 `click,exposure,cost`） |
| `--start-date` `--end-date` | ✅ | `YYYY-MM-DD` |
| `--time-mode` | ✅ | `event` 或 `request`（必须显式传） |
| `--dimensions` | | 英文 code，逗号分隔；`day`/`week`/`month` 是事件时间专用粒度标记 |
| `--filters` | | 见下方"过滤格式" |
| `--page-size` | | 限制返回行数（避免触发 1000 行截断） |
| `--sort-by` `--sort-order` | | 排序：`--sort-by cost --sort-order desc` |
| `--debug` | | 打印完整 REQUEST/RESPONSE JSON 到 stderr，排查用 |

> **⚠️ 维度和指标过滤都支持**：`--filters` 的 key 可以是维度 code（如 `mediaName`、`promotionTarget`、`business_partnership`），也可以是指标 code（如 `cost`、`exposure`）。系统会根据 key 自动路由到 `dimensionCondition` 或 `indicatorCondition`。

#### 过滤格式（推荐键值对）

```
--filters "dim1=值1,值2;dim2=oper:值3"
```

| 语法 | 对应 oper | 例 |
|---|---|---|
| `dim=值` | `EQUAL` | `promotionTarget=问界M7` |
| `dim=in:v1,v2` | `IN` | `promotionTarget=in:问界M7,问界M9` |
| `dim=notin:v1,v2` | `NOT IN` | `business_partnership=notin:尾量` |
| `dim=like:%关键词%` | `LIKE` | `campaignName=like:%新车%` |
| `dim=sw:前缀` `=ew:后缀` | `START WITH` / `END WITH` | |
| `dim=gt:数值` `=gte:` `=lt:` `=lte:` | 比较运算符 | `cost=gt:1000` |
| `dim=null:` `=notnull:` | `IS NULL` / `IS NOT NULL` | |

多条件用分号 `;` 分隔。也支持 JSON 格式（向后兼容）：`--filters '{"promotionTarget":{"oper":"IN","values":["问界M7"]}}'`。

#### 典型示例

```bash
# 商业流水（必须加 business_partnership）
query-metrics --metrics "cost" --start-date "2026-04-01" --end-date "2026-04-07" \
  --time-mode event --dimensions "day" \
  --filters "business_partnership=商业合作"

# 流水 Top20 广告主
query-metrics --metrics "cost" --start-date "2026-04-01" --end-date "2026-04-30" \
  --time-mode event --dimensions "corpName" \
  --filters "business_partnership=商业合作" \
  --sort-by cost --sort-order desc --page-size 20

# 转化分析（必须 request 时间口径）
query-metrics --metrics "cvr,adGroupShallowConversionNumber" \
  --start-date "2026-04-01" --end-date "2026-04-07" \
  --time-mode request --filters "promotionTarget=问界M7"
```

#### 返回结构

```json
{
  "dataset": { "dimensions": [...], "source": [[...], ...] },
  "total": 123,
  "truncated": true,                   // 仅当被截断时存在
  "total_before_truncation": 5000      // 仅当被截断时存在
}
```

`truncated: true` → 数据已截断到 1000 条，禁止据此给全量结论。

### search-dimension-values

**用途有限**：仅当用户给的是**模糊词**、需要的是**精确值**时使用（如用户说"问界" → 找到精确值"问界M7"做 EQUAL 查询）。

```bash
search-dimension-values --dimension "promotionTarget" --query "问界" --top-k 5
```

返回的 `value` 字段直接用于 filters，**不要用 `value_desc`**：

```json
{ "value": "问界M7", "value_desc": "问界M7车型", "similarity": 1 }
```

✅ `--filters "promotionTarget=问界M7"`
❌ `--filters "promotionTarget=问界M7车型"`

#### 🚨 不要用 search 替代 LIKE/NOT LIKE 模式过滤

用户描述包含"包含 / 不包含 / 以 X 开头 / 以 X 结尾"等**模式语义**时，**直接用模式运算符**，不要先 search-dimension-values 枚举所有匹配值再 IN/NOT IN。维度值可能成千上万，枚举必然漏数。

| 用户描述 | ✅ 正确 | ❌ 错误 |
|---|---|---|
| 版位名称不带"非标" | `positionName=notlike:%非标%` | search 列出非标值再 NOT IN（漏） |
| 任务名以"测试"开头 | `adGroupName=sw:测试` | 列出后 IN |
| 包含"618"关键词 | `campaignName=like:%618%` | 同上 |
| 排除测试 / 内测 / 灰度 | `campaignName=notlike:%测试%` 多个分号串联 | search 各种关键词后 NOT IN |
| 非空 / 为空 | `xxx=notnull:` / `xxx=null:` | 列出所有非空值再 IN |

**判断口诀**：用户给的是**关键词 / 模式** → 模式运算符。用户给的是**具体的值（不太确定写法）** → search 找精确值。

### list-metrics / list-dimensions

不知道有哪些指标 / 维度时调用，输出 markdown 表（含 code、name、别名）。**不要在 prompt 里塞静态对照表**——CSV 是单一数据源，业务方更新后这两个工具立即反映。

```bash
list-metrics
list-dimensions
```

## 配置

- `config/metrics.csv` / `dimensions.csv` / `dimension-values.csv`：业务定义。首次部署从对应的 `*.csv.example` 复制。
- `config.json`：含 API secret，**禁止 `git add`**。从 `config.json.example` 复制后填值。
