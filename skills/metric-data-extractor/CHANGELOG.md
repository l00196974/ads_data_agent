# 数据配置和Mock服务优化说明

## 2026-03-08 更新2: 增强CSV文件以支持更好的检索和验证

本次更新对 metrics.csv 和 dimensions.csv 进行了重大增强，添加了丰富的元数据以支持：
- 更好的指标和维度检索（通过别名）
- 外部模型理解（通过描述和分类）
- 指标分类浏览（两级分类体系）
- 维度-指标组合验证（支持的维度列表）
- 维度值校验（枚举值和示例）

### metrics.csv 增强

#### 新增字段
- `metric_desc`: 指标详细描述，说明指标含义和用途
- `category_level1`: 一级分类（基础指标、竞价指标、填充指标等10个类别）
- `category_level2`: 二级分类（点击相关、召回相关、填充率相关等细分类别）
- `supported_dimensions`: 指标支持的维度列表（逗号分隔）
- `metric_aliases`: 指标别名（逗号分隔），支持多种说法匹配

#### 指标分类体系（10个一级类目）
1. **基础指标**: 点击、曝光等基础数据
2. **竞价指标**: 召回、粗排、精排相关
3. **填充指标**: 库存、填充率相关
4. **转化指标**: 转化率、转化数相关
5. **成本指标**: 转化成本、流水相关
6. **价格指标**: ECPM、一二价相关
7. **算法指标**: Bias、预估值相关
8. **出价指标**: 出价因子、调价因子相关
9. **流量指标**: UV、PV相关
10. **其他指标**: 不属于以上类别的指标

#### 字段示例
```csv
click,点击量,"点击量",基础指标,点击相关,"reqDay,reqHour,hour,promotionTarget,mediaName,positionName,priceType,adGroupName,adGroupStatus,corpName,mediaType,slotForm","点击,点击数,点击量,clicks,应收点击数",d1d0b0dde8254f27b67661a3811e8ef4,8
```

### dimensions.csv 增强

#### 新增字段
- `dimension_desc`: 维度详细说明，包含用途和取值范围
- `dimension_aliases`: 维度别名（逗号分隔），支持多种说法匹配
- `value_examples`: 维度值示意
  - 枚举类维度：列出所有可能的值
  - 半枚举类维度：列出常见值并标注"等"
  - 自由值维度：给出2-3个示例并标注"(示例)"
  - ID/日期类维度：说明格式
- `dimension_type`: 维度类型标识
  - `enum`: 枚举类维度，值固定
  - `semi-enum`: 半枚举类维度，常见值有限但可扩展
  - `string`: 自由文本类维度
  - `id`: 标识符类维度
  - `date`: 日期类维度
  - `time`: 时间类维度
  - `package`: 应用包名类维度

#### 字段示例

**枚举类维度**:
```csv
priceType,计费方式,price_type,"计费方式，枚举类维度，值固定","计费方式,计费类型,出价方式","CPC,CPM,CPA,oCPC,oCPM",enum
```

**自由值维度**:
```csv
promotionTarget,推广标的,promotion_target,"推广标的，自由文本类维度","推广标的,推广对象,广告主,标的","问界M7,元保保险,某电商APP(示例)",string
```

**日期类维度**:
```csv
reqDay,请求时间,req_day,"请求时间，日期类维度，格式：YYYY-MM-DD","请求时间,请求日期,日期,天","2026-03-01,2026-03-08(格式:YYYY-MM-DD)",date
```

### 预期效果

#### metrics.csv
1. **支持更好的指标检索**: 通过别名匹配，用户可以用多种说法找到指标
2. **帮助模型理解**: 详细的描述让LLM能够理解指标含义和用途
3. **支持指标分类**: 两级分类让用户可以按类别浏览指标
4. **支持维度验证**: 知道每个指标支持哪些维度，可以提前验证查询合法性
5. **提供更好的错误提示**: 当指标不存在时，可以根据分类推荐相似指标

#### dimensions.csv
1. **支持更好的维度检索**: 通过别名匹配，用户可以用多种说法找到维度
2. **帮助模型理解**: 详细的说明让LLM能够理解维度含义和取值范围
3. **支持枚举值校验**: 对于枚举类维度，可以直接从CSV中获取所有合法值
4. **提供示例和格式**: 对于自由值维度，给出示例和格式说明，帮助用户正确输入
5. **支持维度类型识别**: 通过dimension_type字段，可以区分不同类型的维度

### 文件结构

**metrics.csv** (9个字段):
```
metric_code,metric_name,metric_desc,category_level1,category_level2,supported_dimensions,metric_aliases,indicator_id,decimal_places
```

**dimensions.csv** (7个字段):
```
dimension_code,dimension_name,dimension_en_name,dimension_desc,dimension_aliases,value_examples,dimension_type
```

### 向后兼容性

- 保持原有字段顺序和名称
- 新增字段放在后面
- 现有代码不受影响，可以继续使用原有字段
- 新功能需要更新代码以使用新增字段

---

## 2026-03-08 更新1: 基于官方维度和指标列表的全面重构

本次更新基于华为官方提供的维度列表和指标列表JSON文件,对所有配置文件和Mock服务进行了全面重构,确保与官方定义完全一致。

### 更新概览

- **维度**: 从26个扩展到 **37个官方维度**
- **指标**: 从27个扩展到 **56个官方指标**
- **数据来源**: 基于官方JSON文件 (`维度列表` 和 `指标列表`)
- **字段映射**: 严格按照官方字段名称(name, dimensionEnName, indicatorId等)

### 1. 维度配置 (dimensions.csv)

#### 更新内容
- 包含所有 **37个官方维度**
- 新增 `dimension_en_name` 字段(对应官方dimensionEnName)
- 移除 `value_examples` 字段(移至dimension-values.csv)

#### 字段映射
- `dimension_code` ← 官方 `name` 字段
- `dimension_name` ← 官方 `dimensionName` 字段
- `dimension_en_name` ← 官方 `dimensionEnName` 字段
- `dimension_desc` ← 官方 `uxName` 字段

#### 维度分类

**行业相关 (6个)**
- operatingIndustryLevel1NotConsistent: 一级运营行业
- operatingIndustryLevel2NotConsistent: 二级运营行业
- appFirstIndustryClass: 媒体所属一级行业
- appSecondIndustryClass: 媒体所属二级行业
- appSecondClassName: AG二级分类
- gameMonetizeType: 游戏行业分类

**推广对象相关 (4个)**
- promotionTarget: 推广标的
- promoteAppName: 推广应用名称
- promoteAppPkg: 推广应用包名
- promotionType: 采买模式

**媒体相关 (5个)**
- mediaName: 媒体名称
- mediaType: 媒体分类
- mediaAppName: 媒体应用名称
- mediaAppSecondClass: 二级媒体行业
- packageName: 包名

**版位相关 (5个)**
- positionName: 版位名称
- positionId: 版位id
- slotName: 广告位名称
- slotId: 广告位id
- slotForm: 广告位展示形式

**任务相关 (4个)**
- adGroupName: 任务名称
- adGroupId: 任务ID
- adGroupStatus: 任务状态
- campaignId: 计划id

**转化目标 (2个)**
- shallowEffectType: 浅层转化目标
- deepEffectType: 深层转化目标

**DSP/SSP相关 (3个)**
- dspId: DSP ID
- dspName: dsp名称
- sspName: ssp名称

**其他 (8个)**
- priceType: 计费方式
- algModeTag: 算法模型
- corpId: 广告主id
- corpName: 广告主名称
- reqDay: 请求时间
- reqHour: 请求小时
- hour: 小时分区

### 2. 指标配置 (metrics.csv)

#### 更新内容
- 包含所有 **56个官方指标**
- 新增 `indicator_id` 字段(对应官方indicatorId)
- 新增 `decimal_places` 字段(对应官方decimalPlaces)
- 移除 `supported_dimensions` 和 `usage_restrictions` 字段(待人工补充)

#### 字段映射
- `metric_code` ← 官方 `name` 字段
- `metric_name` ← 官方 `uxName` 字段
- `indicator_id` ← 官方 `indicatorId` 字段
- `decimal_places` ← 官方 `decimalPlaces` 字段

#### 指标分类

**基础指标 (2个)**
- click: 点击量
- receivedExposure: 实收曝光次数

**竞价相关 (9个)**
- recallSumCount: 粗排参与量
- preciseRowParticipation: 精排参与量
- roughRowParticipation: 粗排总量
- roughRowWinRate: 粗排胜出率
- preciseRowWinRate: 精排胜出率
- preciseRankWinRate: 精排胜出率
- impreciseRankWinRate: 粗排胜出率
- recallSumWinRate: 召回胜出率

**填充相关 (9个)**
- adReturnNumber: 广告填充
- mediaReceiveCount: 广告返回个数
- dspReturnCount: DSP广告返回次数
- stockCount: 库存次数
- stockNumber: 库存个数
- adsRequestCount: 广告位请求数
- adRequestNumber: 广告请求个数
- adReturnCount: 广告位返回数

**转化相关 (5个)**
- pbiConvertRate: 转化率
- adGroupShallowConversionNumber: 任务浅层转化目标转化数
- adGroupDeepConversionNumber: 任务深层转化目标转化数
- deepCvr: 深层CVR
- deepCtcvr: 深层CTCVR

**成本相关 (4个)**
- realityConversionCost: 实际转化成本
- shallowCostOffset: 浅层成本抵进
- deepCostOffset: 深层成本抵进
- deepExceptConversionCost: 深层目标转化成本

**价格相关 (6个)**
- realEcpm: real-ecpm
- dspRealEcpm: DSP原始真实出价ECPM
- clickFirstPrice: 点击一价
- clickSecondPrice: 点击二价
- firstSecondPriceGap: 点击一二价GAP
- clickFirstSecondPriceGap: 点击一二价GAP

**流水相关 (4个)**
- actualSpent: 结算点击流水（话单）
- clickActualSpent: 实收点击流水
- revenueGap: 应收实收GAP
- corpValue: 广告主价值

**算法相关 (5个)**
- pctrBias: pctr bias
- pcvrBias: pcvr bias
- finalPCVRBias: final pCVR bias
- resPcvr: 点击resPcvr
- resPcvrBias: 残差pcvrBias

**出价相关 (4个)**
- pacer1: 出价因子
- costRatio: 调价因子（点击口径）
- costRatioCnt: 调价因子数（点击口径）
- feeDeductionRatio: 扣费因子

**填充率相关 (4个)**
- adFillRateCount: 填充率（次数）
- adFillRateNumber: 填充率（个数）
- adImpRateCount: 展示率（次数）
- adImpRateNumber: 展示率（个数）

**UV/PV (3个)**
- dspReqUV: DSP请求UV
- dspReqPV: DSP请求次数
- activeUV: 大盘活跃UV

**RTA (1个)**
- rtaBidRate: RTA参竞率

**其他 (1个)**
- totalOcpxAdvTargetCnvrPrice: 广告浅层目标转化价格

### 3. 维度值数据 (dimension-values.csv)

#### 更新内容
- 为所有37个官方维度提供示例值
- 简化字段结构,移除 `value_code` 和 `value_aliases`
- 保留 `dimension_code`, `value`, `value_desc` 三个核心字段

#### 数据特点
- 每个维度提供3-7个示例值
- 值为中文或实际业务值(如包名、ID等)
- 便于人工维护和扩充

### 4. Mock服务优化 (mock/mock-server.js)

#### 更新内容
1. **dimensionValuesFor()函数**: 支持所有37个官方维度
2. **baseValue()函数**: 支持所有56个官方指标
3. **智能数据生成策略**:
   - 比率类指标(包含Rate, Bias, CVR, CTR等): 0-1之间,保留8位小数
   - 因子类指标(pacer, ratio等): 0.5-2之间
   - 价格/成本类指标(包含Price, Cost, Ecpm等): 中等数值,±10%波动
   - 计数类指标(包含Count, Number, UV, PV等): 大数值,±15%波动
   - 所有指标默认保留8位小数(与官方定义一致)

#### 数据生成逻辑
```javascript
// 比率类指标: 0-1之间
if (indicatorKey.includes('rate') || indicatorKey.includes('bias') ||
    indicatorKey.includes('cvr') || indicatorKey.includes('ctr'))

// 因子类指标: 0.5-2之间
if (['pacer1', 'costRatio', 'feeDeductionRatio'].includes(indicatorKey))

// 价格类指标: ±10%波动
if (indicatorKey.includes('price') || indicatorKey.includes('cost') ||
    indicatorKey.includes('ecpm'))

// 计数类指标: ±15%波动
if (indicatorKey.includes('count') || indicatorKey.includes('number') ||
    indicatorKey.includes('uv') || indicatorKey.includes('pv'))
```

### 5. 测试场景 (mock/mock-data.json)

#### 更新内容
- 从10个场景扩展到 **12个测试场景**
- 所有场景使用官方维度和指标
- 覆盖主要业务分析场景

#### 场景列表
1. **basic-effect-analysis**: 基础效果分析 - 点击、曝光、转化
2. **bidding-analysis**: 竞价分析 - 召回、粗排、精排
3. **cost-analysis**: 成本分析 - 转化成本、流水
4. **fill-analysis**: 填充分析 - 库存、填充率
5. **deep-conversion-analysis**: 深层转化分析
6. **price-analysis**: 价格分析 - ECPM、一二价
7. **algorithm-analysis**: 算法分析 - Bias指标
8. **bidding-factor-analysis**: 出价因子分析
9. **media-analysis**: 媒体分析 - 按媒体和版位
10. **industry-analysis**: 行业分析
11. **uv-pv-analysis**: UV/PV分析
12. **rta-analysis**: RTA参竞率分析

### 6. 文档更新 (mock/README.md)

#### 更新内容
- 列出所有37个官方维度及其说明
- 列出所有56个官方指标及其分类
- 更新请求示例使用官方字段名
- 说明数据生成策略和约束规则
- 强调基于官方定义构建

## 使用方式

### 启动Mock服务
```bash
cd skills/metric-data-extractor
node mock/mock-server.js
```

### 查询示例
```bash
curl -X POST http://localhost:3000/ads-data/openapi/v1/chart/common \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [
      {"indicatorKey": "click"},
      {"indicatorKey": "receivedExposure"},
      {"indicatorKey": "pbiConvertRate"}
    ],
    "dimensions": ["reqDay", "priceType"],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-07"
    }]
  }'
```

## 数据来源

所有配置基于以下官方文件:
- `/home/linxiankun/huawei-ad-ontology/维度列表`: 37个官方维度定义
- `/home/linxiankun/huawei-ad-ontology/指标列表`: 56个官方指标定义

## 验证清单

- [x] dimensions.csv 包含所有37个官方维度
- [x] metrics.csv 包含所有56个官方指标
- [x] 字段名与官方JSON完全一致
- [x] dimension-values.csv 为所有维度提供示例值
- [x] mock-server.js 支持所有维度和指标
- [x] mock-data.json 使用官方字段名
- [x] README.md 列出所有官方维度和指标
- [x] 所有指标保留8位小数(与官方定义一致)

## 人工维护说明

这些配置文件设计为可由人工维护:

1. **dimensions.csv**: 如有新维度,按相同格式添加即可
2. **metrics.csv**: 如有新指标,按相同格式添加即可
3. **dimension-values.csv**: 可随时添加更多维度值
4. **mock-server.js**:
   - 在 `dimensionValuesFor()` 中添加新维度的枚举值
   - 在 `baseValue()` 的 `seed` 对象中添加新指标的基准值

## 兼容性说明

- Mock服务API接口保持不变
- 返回数据格式与真实API一致
- 向后兼容,不影响现有代码
- 仅扩展了支持的维度和指标范围

## 文件清单

```
skills/metric-data-extractor/
├── config/
│   ├── dimensions.csv          (37个官方维度)
│   ├── metrics.csv             (56个官方指标)
│   └── dimension-values.csv    (所有维度的示例值)
├── mock/
│   ├── mock-server.js          (支持所有官方维度和指标)
│   ├── mock-data.json          (12个测试场景)
│   └── README.md               (完整使用文档)
└── CHANGELOG.md                (本文件)
```
