
---
name: metric-data-extractor
description: 华为广告数据查询工具。当用户需要查询具体的广告指标数据时使用，包括：查询点击量、曝光量、消耗、转化数等指标；按时间、渠道、产品等维度拆解数据；搜索推广标的、媒体等维度值；对比不同产品或渠道的投放效果。支持自然语言查询，自动处理指标映射和维度搜索。适用场景：数据查询、指标提取、维度搜索、效果对比。不适用于：问题诊断分析、数据计算（占比、同比等）、报告生成、策略建议。
metadata: {}
user-invocable: true
---

# 核心指标数据提取

作为数据分析的"双手"，本 Skill 提供统一的指标查询接口，自动处理实体对齐、语义检索、API 认证和错误纠正。

## 🚨 重要：LLM使用指南

### ⚠️ 约束：禁止自行创造指标

**在回答用户问题时，必须遵循以下规则：**

1. **禁止自行创造指标计算公式**
   - ❌ 禁止：自行定义 "ROI = 转化数/花费"、"CPM = cost/exposure*1000" 等公式
   - ❌ 禁止：使用系统中不存在的指标别名
   - ✅ 必须：使用配置文件中定义的指标代码

2. **遇到不确定问题时的处理方式**
   - 如果用户询问的指标在文档中没有明确说明，**必须先询问用户确认**
   - 提供备选方案让用户选择，不要假设用户的意图
   - 示例：用户问"ROI是多少" → 应先问"您想用哪个公式计算ROI？A)广告主价值/花费 B)转化数/花费 C)其他方式"

3. **指标查询优先级**
   - 首先使用系统提供的原生指标（如 `corpValue`、`shallowCostOffset`、`cvr` 等）
   - 只有在用户明确要求且系统没有对应指标时，才能提及需要自行计算

### 数据查询工作流程

**当用户提出数据查询需求时，按以下顺序执行：**

1. **确认时间口径** - 根据用户意图选择 `event` 或 `request` 时间口径
2. **确认维度值**（如需要）- 使用 `search_dimension_values` 确认具体的维度值
3. **执行查询** - 调用 `query_metrics` 进行数据查询
4. **获取更多信息**（如需要）- 使用 `list_metrics` 或 `list_dimensions` 了解系统能力

### 🚨 重要：过滤条件和分页限制规则（必读！）

**⚠️ 核心原则：查询时必须尽可能完善过滤条件，避免返回数据超出API分页上限导致数据丢失**

#### 1. 为什么不能全部查出来再过滤？

- API 返回结果有**分页上限**（通常为2000-5000条记录）
- 如果查询条件过于宽泛，返回的记录可能**超过分页上限**
- 超过上限后，**数据会被截断**，后面的数据不会返回
- 使用 `grep` / `head` / `tail` 等工具**只是过滤已返回的数据**，无法获取被截断的数据

#### 2. 查询时如何控制数据量？

**必须遵循的原则：先过滤，后查询**

❌ **错误示例（会导致数据截断）：**
```bash
# 查询所有广告主，数据量可能超过分页上限
query-metrics --metrics "corpValue,cost" --start-date "2026-03-01" --end-date "2026-03-31" --dimensions "corpName"

# 然后用grep过滤 - 但数据已经被截断了！
query-metrics ... | grep "某个广告主"
```

✅ **正确示例（使用完善的过滤条件）：**
```bash
# 方式1：添加媒体过滤
query-metrics --metrics "corpValue,cost" --start-date "2026-03-01" --end-date "2026-03-31" --dimensions "corpName" --filters "mediaName=华为浏览器"

# 方式2：添加时间范围过滤（缩小范围）
query-metrics --metrics "corpValue,cost" --start-date "2026-03-01" --end-date "2026-03-07" --dimensions "corpName" --filters "mediaName=华为浏览器"

# 方式3：使用排序+TopN限制返回数量
query-metrics --metrics "corpValue,cost" --start-date "2026-03-01" --end-date "2026-03-31" --dimensions "corpName" --filters "mediaName=华为浏览器" --sort-by "cost" --sort-order "desc" --top-n 100

# 方式4：组合多个过滤条件
query-metrics --metrics "corpValue,cost" --start-date "2026-03-01" --end-date "2026-03-31" --dimensions "corpName" --filters "mediaName=华为浏览器;business_partnership=商业合作"
```

#### 3. 如何判断数据是否被截断？

查询完成后，检查返回结果：
- 如果返回数据的 `total` 字段**接近或等于分页上限**，说明可能还有更多数据未返回
- 使用 `--page-size` 参数可以明确限制返回数量，避免截断
- 如果确实需要获取全量数据，考虑**分批次查询**（按时间分段、按媒体分段等）

#### 4. 必须添加过滤条件的典型场景

| 场景 | 必须添加的过滤条件 |
|------|-------------------|
| 查询某个媒体的数据 | `--filters "mediaName=华为浏览器"` |
| 查询某个推广标的 | `--filters "promotionTarget=问界M7"` |
| 查询商业广告 | `--filters "business_partnership=商业合作"` |
| 查询某个广告主 | `--filters "corpName=xxx"` 或 `--filters "corpId=xxx"` |
| 查询某个时间段 | 缩小 `--start-date` 和 `--end-date` 范围 |

#### 5. 排序和分页的使用

当用户需要"排名"、"前N名"等数据时，**必须配合排序和分页**：

```bash
# 查询流水排名前20的广告主
query-metrics --metrics "corpValue,cost" --start-date "2026-03-01" --end-date "2026-03-31" \
  --dimensions "corpName" \
  --filters "mediaName=华为浏览器;business_partnership=商业合作" \
  --sort-by "cost" --sort-order "desc" --page-size 20
```

**注意**：`--page-size` 参数可以有效限制返回记录数，避免数据过多导致超时

#### 6. 分批次查询策略

如果需要查询大量数据，可以分批次查询：

```bash
# 按周分段查询
query-metrics ... --start-date "2026-03-01" --end-date "2026-03-07" ...
query-metrics ... --start-date "2026-03-08" --end-date "2026-03-14" ...
query-metrics ... --start-date "2026-03-15" --end-date "2026-03-21" ...
query-metrics ... --start-date "2026-03-22" --end-date "2026-03-28" ...
query-metrics ... --start-date "2026-03-29" --end-date "2026-04-04" ...
```

### 🚨 重要：商业广告过滤规则

**华为广告平台包含大量非商业性质的广告流水，这部分数据：**
- 不会纳入经营分析
- 使用的是广告尾量
- 不会用来衡量广告效果

**因此，当用户询问以下问题时，必须添加商业合作过滤条件：**
- ❌ 错误：直接查询 `cost`（会包含非商业广告数据）
- ✅ 正确：添加 `--filters "business_partnership=商业合作"`

**常见需要过滤的商业广告场景：**
- 商业广告流水
- 商业广告的点击量、曝光量
- 商业广告的 CPM、CVR 等效果指标
- 任何用于经营分析的指标数据

**过滤示例：**
```bash
# 查询商业广告流水
query-metrics --metrics "cost" --start-date "2026-04-08" --end-date "2026-04-08" --time-mode event --filters "business_partnership=商业合作"

# 查询商业广告的点击和曝光
query-metrics --metrics "click,exposure" --start-date "2026-04-08" --end-date "2026-04-08" --time-mode event --filters "business_partnership=商业合作"
```

### 关键规则

**必须使用英文代码：**
- ✅ 指标：`click,exposure,cost`
- ❌ 错误：`点击量,曝光,流水`
- ✅ 维度：`promotionTarget,reqDay,mediaName`
- ❌ 错误：`推广标的,请求时间,媒体名称`
- ✅ 过滤：`{"promotionTarget": ["问界M7"]}`
- ❌ 错误：`{"推广标的": ["问界M7"]}`

**常见错误代码对照：**
- ❌ `clicks` → ✅ `click`
- ❌ `impressions` / `receivedExposure` → ✅ `exposure`
- ❌ `actualSpent` / `clickActualSpent` → ✅ `cost`
- ❌ `pbiConvertRate` → ✅ `cvr`
- ❌ `product_name` → ✅ `promotionTarget`

**⚠️ CRITICAL: day 和 reqDay 的区别（常见错误）：**

这是最容易混淆的地方，请务必理解：

- **`day`** — 事件时间口径（`event`）专用的**特殊标记**，不是真实的 API 维度
  - 传入 `--dimensions "day"` 后，系统自动设置 `timingDimension=day`
  - 返回结果中会包含 `date` 字段（格式：`YYYY-MM-DD`）
  - ✅ 正确：`--time-mode event --dimensions "day"`
  - ❌ 错误：`--time-mode event --dimensions "reqDay"`（事件时间不要用 reqDay！）

- **`reqDay`** — 请求时间口径（`request`）专用的**真实 API 维度**
  - 表示广告请求发生的日期
  - 请求时间口径下，系统会自动注入 `reqDay`，通常不需要手动传
  - ✅ 正确：`--time-mode request`（系统自动注入 reqDay）
  - ✅ 也可以：`--time-mode request --dimensions "reqDay"`（显式传入）
  - ❌ 错误：`--time-mode request --dimensions "day"`（请求时间不要用 day！）

**记忆口诀**：
- 事件时间（event）用 `day`
- 请求时间（request）用 `reqDay` 或不传（自动注入）
- 两者不能混用！

## 使用场景

- 查询广告消耗、线索量、展现量、点击量等核心指标
- 按时间、渠道、推广对象、创意等维度拆解数据
- 支持自然语言参数，自动映射到 API 字段
- 当维度值不匹配时，自动语义搜索相似值

### 日期快捷方式

当用户使用"最近N天"、"上周"、"上月"等自然语言时，模型需要自动计算具体日期。

**⚠️ 重要：日期需要往前偏移一天**

因为当天的数据通常还没有同步到数据库，所以：
- `end-date` 应该是 **昨天**（today - 1）
- `start-date` 应该是 **昨天往前推 N-1 天**

| 用户表达 | 计算方式 | 示例（假设今天2026-04-08） |
|----------|----------|---------------------------|
| 最近7天 | (yesterday-6) ~ yesterday | 2026-04-01 ~ 2026-04-07 |
| 最近30天 | (yesterday-29) ~ yesterday | 2026-03-09 ~ 2026-04-07 |
| 最近3天 | (yesterday-2) ~ yesterday | 2026-04-05 ~ 2026-04-07 |
| 上周 | 上周一 ~ 上周日 | 2026-03-30 ~ 2026-04-05 |
| 上月 | 上月1日 ~ 上月末 | 2026-03-01 ~ 2026-03-31 |

**计算公式**：
- `end-date` = today - 1
- `start-date` = today - 1 - (N - 1) = today - N

**注意**：模型需要根据当前实际日期进行计算，不要假设固定日期。

## 时间口径说明

广告数据有两种时间口径，查询前必须确认用户意图：

### 事件发生时间口径（`--time-mode event`）
按事件实际发生日期统计。例如：3月1日的曝光 + 3月2日的付费，按事件时间查则3月1日有曝光、3月2日有转化。

**适用场景**：关注实际曝光/点击/转化发生的分布。

**示例**（按天分组需在 `--dimensions` 里传 `day`）：
```bash
query-metrics --metrics "click,receivedExposure" \
  --start-date "2026-03-01" --end-date "2026-03-07" \
  --time-mode event --dimensions "day"
```

### 广告请求时间口径（`--time-mode request`）
按广告请求发生的日期汇总，转化归属到请求日。广告主回传可能延迟，内部自动将 dateTimeFilter 结束时间延长 90 天，并通过 filterConditions 限制 reqDay 范围。

**适用场景**：关注某天的广告投放请求带来的整体效果（含延迟转化）。

**示例**（`reqDay` 维度自动注入，无需手动传）：
```bash
query-metrics --metrics "click,adGroupShallowConversionNumber" \
  --start-date "2026-03-01" --end-date "2026-03-07" \
  --time-mode request
```

### 🎯 如何判断该用哪种口径？（重要！）

**核心判断标准**：用户关心的是"事件发生时间"还是"广告投放归因"？

#### 使用事件时间（event）的场景：
- 用户问"3月1日有多少曝光" → 事件时间 (`event`)
- 用户问"按天展示点击量趋势" → 事件时间 (`event`)
- 用户问"查看每天的点击数据" → 事件时间 (`event`)
- 用户问"某个时间段的曝光量" → 事件时间 (`event`)
- **关键词**：曝光、点击、展现、发生、实际

#### 使用请求时间（request）的场景：
- 用户问"3月1日的广告带来了多少转化" → 请求时间 (`request`)
- 用户问"每天的投放效果" → 请求时间 (`request`)
- 用户问"统计投放带来的转化" → 请求时间 (`request`)
- 用户问转化成本/CPA/ROI 等归因指标 → 请求时间 (`request`)
- 用户问"某天投放的转化率" → 请求时间 (`request`)
- **关键词**：转化、归因、投放效果、带来、CPA、ROI、转化率

#### 特别注意：
- **转化类指标（转化数、转化率、CPA）必须使用请求时间（request）**
- 如果用户同时查询点击和转化，优先使用请求时间（request）
- 默认情况下使用事件时间（event），除非明确涉及转化归因

### 🚨 重要：区分"计费方式"和"采买模式"（常见错误！）

这是另一个容易混淆的地方：

#### `priceType`（计费方式/出价策略）
- 定义：广告的出价方式，如 CPC、CPM、CPA、oCPC 等
- **⚠️ 注意**：CPM 计费方式可能是竞价广告，也可能是合约广告，**不能直接用这个维度来区分竞价和合约**
- 示例值：CPC、CPM、CPA、oCPC、CPT、CPD 等

#### `promotionType`（采买模式）
- 定义：**区分竞价广告和合约广告的核心维度**
- ✅ 正确用途：查看竞价 vs 合约的流量和消耗对比
- 示例值：`竞价`、`合约`、`定价采买`、`分成`
- **查询示例**：查看竞价和合约的流水差异
  ```bash
  query-metrics --metrics "cost,cpm" --start-date "2026-04-08" --end-date "2026-04-08" --time-mode event --dimensions "promotionType"
  ```

#### 记忆口诀：
- **计费方式 (priceType)** = 怎么收费（CPC按点击、CPM按曝光等）
- **采买模式 (promotionType)** = 怎么买（竞价/合约）- 用于区分广告类型

### timingDimension 自动设置规则
当 `--time-mode event` 且 `--dimensions` 包含时间粒度标记时：
- 含 `day` → 按天汇总，`timingDimension=day`，返回数据含 `date` 字段（格式：`YYYY-MM-DD`，如 `2026-03-07`）
- 含 `week` → 按周汇总，`timingDimension=week`，返回数据含 `date` 字段（格式：`YYYY-MM-DD~YYYY-MM-DD`，如 `2026-03-01~2026-03-07`）
- 含 `month` → 按月汇总，`timingDimension=month`，返回数据含 `date` 字段（格式：`YYYYMM`，如 `202603`）

**注意**：`day`/`week`/`month` 不是 API 维度，系统会自动将它们从 dimensions 中移除并转为 `timingDimension` 参数。API 返回中会额外增加 `date` 字段。

**事件时间口径下不要传 `reqDay`**。`reqDay` 仅用于广告请求时间口径（`request`）。

## 工具

### query-metrics

查询华为广告指标数据。

**用法：**

Linux / macOS：

```bash
query-metrics \
  --metrics "click,exposure,cost" \
  --start-date "2026-01-01" \
  --end-date "2026-01-15" \
  --dimensions "reqDay,promotionTarget" \
  --filters "promotionTarget=问界M7"
```

Windows PowerShell（请使用单行命令，避免续行符导致参数解析错误）：

```powershell
query-metrics --metrics "click,exposure,cost" --start-date "2026-01-01" --end-date "2026-01-15" --time-mode event --dimensions "day,promotionTarget" --filters "promotionTarget=问界M7"
```

**🚨 重要：filters 参数格式说明**

为避免 JSON 引号转义问题，**必须使用键值对格式**（LLM 更容易生成正确的命令）：

**键值对格式（唯一推荐）：**
```bash
--filters "dimension1=value1,value2;dimension2=value3"
```

**支持的过滤运算符：**

| 语法 | API oper | 说明 |
|-----|---------|------|
| `dimension=值` | `EQUAL` | 精确匹配（默认） |
| `dimension=in:值1,值2` | `IN` | 在列表中 |
| `dimension=notin:值1,值2` | `NOT IN` | 不在列表中 |
| `dimension=like:%关键词%` | `LIKE` | 模糊匹配 |
| `dimension=notlike:%关键词%` | `NOT LIKE` | 模糊不匹配 |
| `dimension=sw:前缀` | `START WITH` | 以某值开头 |
| `dimension=ew:后缀` | `END WITH` | 以某值结尾 |
| `dimension=nsw:前缀` | `NOT START WITH` | 不以某值开头 |
| `dimension=new:后缀` | `NOT END WITH` | 不以某值结尾 |
| `dimension=gt:数值` | `GREATER THAN` | 大于 |
| `dimension=gte:数值` | `GREATER THAN OR EQUAL` | 大于等于 |
| `dimension=lt:数值` | `LESS THAN` | 小于 |
| `dimension=lte:数值` | `LESS THAN OR EQUAL` | 小于等于 |
| `dimension=null:` | `IS NULL` | 为空 |
| `dimension=notnull:` | `IS NOT NULL` | 不为空 |

**使用示例：**
- 单个维度单个值：`--filters "promotionTarget=问界M7"`
- 单个维度多个值（IN）：`--filters "promotionTarget=in:问界M7,问界M9"`
- 排除某些值（NOT IN）：`--filters "business_partnership=notin:商业合作"`
- 模糊匹配：`--filters "campaignName=like:%新车%"`
- 数值比较：`--filters "cost=gt:1000"`
- 多维度组合：`--filters "promotionType=合约;business_partnership=notin:商业合作"`

**注意**：
- 运算符与值之间用冒号 `:` 分隔
- 多个过滤条件用分号 `;` 分隔
- 也可以使用 JSON 格式：`--filters "{\"promotionType\":{\"oper\":\"IN\",\"values\":[\"合约\"]}}"`

**查询示例：**

1. **基础效果查询（问界M7点击和曝光，按天查看）：**
```bash
query-metrics --metrics "click,exposure" --start-date "2026-03-01" --end-date "2026-03-07" --time-mode event --dimensions "day" --filters "promotionTarget=问界M7"
```

2. **成本分析查询：**
```bash
query-metrics --metrics "cost,cvr" --start-date "2026-03-01" --end-date "2026-03-07" --time-mode request --dimensions "reqDay" --filters "promotionTarget=问界M7"
```

3. **媒体对比查询：**
```bash
query-metrics --metrics "click,exposure" --start-date "2026-03-01" --end-date "2026-03-05" --time-mode event --dimensions "mediaName"
```

4. **多维度过滤查询：**
```bash
query-metrics --metrics "click,exposure,cost" --start-date "2026-03-01" --end-date "2026-03-07" --time-mode event --dimensions "day" --filters "promotionTarget=问界M7,问界M9;mediaName=抖音"
```

**参数：**

- `--metrics`: 指标列表，逗号分隔（**必须使用英文指标代码，不要使用中文名称**）
- `--start-date`: 开始日期，格式 `YYYY-MM-DD`
- `--end-date`: 结束日期，格式 `YYYY-MM-DD`
- `--time-mode`: 时间口径，`event`（事件发生时间）或 `request`（广告请求时间），**必须显式传入**
- `--dimensions`: 维度列表，逗号分隔，可选（**必须使用英文维度代码**）
- `--filters`: 过滤条件 JSON，可选

**常用指标英文代码对照表：**

| 中文名称 | 英文代码 | 说明 |
|---------|---------|------|
| 点击量 | `click` | 基础点击指标 |
| 曝光 | `exposure` | 曝光量指标 |
| 流水 | `cost` | 成本指标 |
| 曝光率 | `exposureRate` | 曝光率指标 |
| 点击率 | `clickRate` | 点击率指标 |
| CVR | `cvr` | 转化率指标 |
| pctr bias | `pctrBias` | 算法预估点击率偏差 |
| pcvr bias | `pcvrBias` | 算法预估转化率偏差 |
| CPM | `cpm` | 千次曝光成本 |
| eCPM | `ecpm` | 有效千次曝光成本 |
| 广告主价值 | `corpValue` | 广告主获得的商业价值=转化数量×转化目标出价 |
| 浅层成本抵近 | `shallowCostOffset` | 广告主花费/广告主价值，越小说明广告主收益越高 |
| 首要转化目标转化数 | `adGroupShallowConversionNumber` | 任务首要转化目标的转化数量 |
| 首要转化目标转化成本 | `realityConversionCost` | 每转化成本=花费/转化数 |
| 深层转化目标转化成本 | `deepExceptConversionCost` | 每深层转化成本 |
| 深层转化目标转化数 | `adGroupDeepConversionNumber` | 任务次要转化目标的转化数量 |

**常用维度英文代码对照表：**

| 中文名称 | 英文代码 | 说明 |
|---------|---------|------|
| 按天（事件时间） | `day` | 事件时间口径专用，映射到 timingDimension，返回 date 字段 |
| 按周（事件时间） | `week` | 事件时间口径专用，映射到 timingDimension，返回 date 字段 |
| 按月（事件时间） | `month` | 事件时间口径专用，映射到 timingDimension，返回 date 字段 |
| 请求时间 | `reqDay` | 请求时间口径专用，真实 API 维度 |
| 推广标的 | `promotionTarget` | 推广对象 |
| 媒体名称 | `mediaName` | 媒体渠道。**注意**：华为浏览器在不同数据中可能显示为"华为浏览器"或"HUAWEI Browser"，查询完整数据时需要同时指定这两个值 |
| 计费方式 | `priceType` | 计费类型。注意：CPM计费方式可能是竞价或合约，区分竞价和合约请使用promotionType |
| 采买模式 | `promotionType` | 区分竞价和合约的核心维度，用于查看竞价vs合约的流量和消耗 |
| 任务名称 | `adGroupName` | 广告任务 |

### search-dimension-values

语义搜索维度值。

**🚨 重要：使用搜索结果时，必须使用 `value` 字段而不是 `value_desc` 字段进行后续查询！**

**用法：**

Linux / macOS：

```bash
search-dimension-values --dimension "promotionTarget" --query "保险产品" --top-k 5
```

Windows PowerShell（请使用单行命令，避免续行符导致参数解析错误）：

```powershell
search-dimension-values --dimension "promotionTarget" --query "保险产品" --top-k 5
```

**返回格式示例：**
```json
{
  "dimension": "promotionTarget",
  "query": "问界",
  "results": [
    {
      "value": "问界M7",           // ← 用这个值进行查询！
      "value_desc": "问界M7车型",  // ← 不要用这个描述！
      "similarity": 1
    }
  ]
}
```

**🚨 关键规则：**
- ✅ 正确：使用 `value` 字段 → `{"promotionTarget": ["问界M7"]}`
- ❌ 错误：使用 `value_desc` 字段 → `{"promotionTarget": ["问界M7车型"]}`

**参数：**

- `--dimension`: 维度编码（必须使用从 list_dimensions 获取的准确代码）
- `--query`: 搜索关键词
- `--top-k`: 返回结果数，默认 5

### list-metrics

列出所有可用的指标。

**用法：**

```bash
list-metrics
``` 

### list-dimensions

列出所有可用的维度。

**用法：**

```bash
list-dimensions
``` 

## 配置文件

- `config/metrics.csv`: 指标定义（首次使用需从 `metrics.csv.example` 复制）
- `config/dimensions.csv`: 维度定义（首次使用需从 `dimensions.csv.example` 复制）
- `config/dimension-values.csv`: 维度值库（首次使用需从 `dimension-values.csv.example` 复制）

**注意**：真实配置文件已加入 `.gitignore`，不会被提交到 Git。

## 典型流程

1. 使用 `query-metrics` 输入自然语言指标、维度与过滤条件。
2. 工具先执行实体对齐。
3. 如果过滤值无法精确匹配，工具自动尝试 `search-dimension-values` 做语义修复。
4. 组装 DSL 并根据 config.json 配置调用 API 服务。
5. 返回 ECharts dataset 格式的结构化 JSON 结果。
