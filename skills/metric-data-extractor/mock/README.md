# Mock 服务使用说明

## 概述

Mock 服务模拟华为广告数据 API,支持所有官方维度和指标的查询测试。本服务基于华为官方提供的维度列表和指标列表构建。

**最新更新**: CSV配置文件已增强，添加了丰富的元数据（描述、别名、分类、枚举值等），详见 [CHANGELOG.md](../CHANGELOG.md)。

## 配置文件说明

### metrics.csv (指标配置)
包含56个官方指标，每个指标包含以下字段：
- `metric_code`: 指标编码
- `metric_name`: 指标名称
- `metric_desc`: 指标详细描述
- `category_level1`: 一级分类（基础指标、竞价指标、填充指标等）
- `category_level2`: 二级分类（点击相关、召回相关等）
- `supported_dimensions`: 支持的维度列表
- `metric_aliases`: 指标别名（支持多种说法）
- `indicator_id`: 官方指标ID
- `decimal_places`: 小数位数

### dimensions.csv (维度配置)
包含37个官方维度，每个维度包含以下字段：
- `dimension_code`: 维度编码
- `dimension_name`: 维度名称
- `dimension_en_name`: 维度英文名
- `dimension_desc`: 维度详细说明
- `dimension_aliases`: 维度别名（支持多种说法）
- `value_examples`: 维度值示意（枚举值或示例）
- `dimension_type`: 维度类型（enum/semi-enum/string/id/date/time/package）

### dimension-values.csv (维度值配置)
为每个维度提供具体的可选值，用于校验外部模型输入。

## 启动服务

```bash
cd skills/metric-data-extractor
node mock/mock-server.js
```

默认端口: 3000 (可通过环境变量 PORT 修改)

## 支持的维度 (共37个)

基于官方维度列表,支持以下维度:

### 行业相关
- **operatingIndustryLevel1NotConsistent**: 一级运营行业
- **operatingIndustryLevel2NotConsistent**: 二级运营行业
- **appFirstIndustryClass**: 媒体所属一级行业
- **appSecondIndustryClass**: 媒体所属二级行业
- **appSecondClassName**: AG二级分类
- **gameMonetizeType**: 游戏行业分类

### 推广对象相关
- **promotionTarget**: 推广标的
- **promoteAppName**: 推广应用名称
- **promoteAppPkg**: 推广应用包名
- **promotionType**: 采买模式

### 媒体相关
- **mediaName**: 媒体名称
- **mediaType**: 媒体分类
- **mediaAppName**: 媒体应用名称
- **mediaAppSecondClass**: 二级媒体行业
- **packageName**: 包名

### 版位相关
- **positionName**: 版位名称
- **positionId**: 版位id
- **slotName**: 广告位名称
- **slotId**: 广告位id
- **slotForm**: 广告位展示形式

### 任务相关
- **adGroupName**: 任务名称
- **adGroupId**: 任务ID
- **adGroupStatus**: 任务状态
- **campaignId**: 计划id

### 转化目标
- **shallowEffectType**: 浅层转化目标
- **deepEffectType**: 深层转化目标

### DSP/SSP相关
- **dspId**: DSP ID
- **dspName**: dsp名称
- **sspName**: ssp名称

### 其他
- **priceType**: 计费方式
- **algModeTag**: 算法模型
- **corpId**: 广告主id
- **corpName**: 广告主名称
- **reqDay**: 请求时间
- **reqHour**: 请求小时
- **hour**: 小时分区

## 支持的指标 (共56个)

基于官方指标列表,支持以下指标:

### 基础指标
- **click**: 点击量
- **receivedExposure**: 实收曝光次数

### 竞价相关
- **recallSumCount**: 粗排参与量
- **preciseRowParticipation**: 精排参与量
- **roughRowParticipation**: 粗排总量
- **roughRowWinRate**: 粗排胜出率
- **preciseRowWinRate**: 精排胜出率
- **preciseRankWinRate**: 精排胜出率
- **impreciseRankWinRate**: 粗排胜出率
- **recallSumWinRate**: 召回胜出率

### 填充相关
- **adReturnNumber**: 广告填充
- **mediaReceiveCount**: 广告返回个数
- **dspReturnCount**: DSP广告返回次数
- **stockCount**: 库存次数
- **stockNumber**: 库存个数
- **adsRequestCount**: 广告位请求数
- **adRequestNumber**: 广告请求个数
- **adReturnCount**: 广告位返回数

### 转化相关
- **pbiConvertRate**: 转化率
- **adGroupShallowConversionNumber**: 任务浅层转化目标转化数
- **adGroupDeepConversionNumber**: 任务深层转化目标转化数
- **deepCvr**: 深层CVR
- **deepCtcvr**: 深层CTCVR

### 成本相关
- **realityConversionCost**: 实际转化成本
- **shallowCostOffset**: 浅层成本抵进
- **deepCostOffset**: 深层成本抵进
- **deepExceptConversionCost**: 深层目标转化成本

### 价格相关
- **realEcpm**: real-ecpm
- **dspRealEcpm**: DSP原始真实出价ECPM
- **clickFirstPrice**: 点击一价
- **clickSecondPrice**: 点击二价
- **firstSecondPriceGap**: 点击一二价GAP
- **clickFirstSecondPriceGap**: 点击一二价GAP

### 流水相关
- **actualSpent**: 结算点击流水（话单）
- **clickActualSpent**: 实收点击流水
- **revenueGap**: 应收实收GAP
- **corpValue**: 广告主价值

### 算法相关
- **pctrBias**: pctr bias
- **pcvrBias**: pcvr bias
- **finalPCVRBias**: final pCVR bias
- **resPcvr**: 点击resPcvr
- **resPcvrBias**: 残差pcvrBias

### 出价相关
- **pacer1**: 出价因子
- **costRatio**: 调价因子（点击口径）
- **costRatioCnt**: 调价因子数（点击口径）
- **feeDeductionRatio**: 扣费因子

### 填充率相关
- **adFillRateCount**: 填充率（次数）
- **adFillRateNumber**: 填充率（个数）
- **adImpRateCount**: 展示率（次数）
- **adImpRateNumber**: 展示率（个数）

### UV/PV
- **dspReqUV**: DSP请求UV
- **dspReqPV**: DSP请求次数
- **activeUV**: 大盘活跃UV

### RTA
- **rtaBidRate**: RTA参竞率

### 其他
- **totalOcpxAdvTargetCnvrPrice**: 广告浅层目标转化价格

## 请求示例

### 基础查询
```bash
curl -X POST http://localhost:3000/ads-data/openapi/v1/chart/common \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [{"indicatorKey": "click"}],
    "dimensions": ["reqDay"],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-07"
    }]
  }'
```

### 竞价分析查询
```bash
curl -X POST http://localhost:3000/ads-data/openapi/v1/chart/common \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [
      {"indicatorKey": "recallSumCount"},
      {"indicatorKey": "roughRowParticipation"},
      {"indicatorKey": "preciseRowParticipation"},
      {"indicatorKey": "roughRowWinRate"},
      {"indicatorKey": "preciseRowWinRate"}
    ],
    "dimensions": ["reqDay", "priceType"],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-05"
    }]
  }'
```

### 带过滤条件查询
```bash
curl -X POST http://localhost:3000/ads-data/openapi/v1/chart/common \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [
      {"indicatorKey": "click"},
      {"indicatorKey": "receivedExposure"},
      {"indicatorKey": "pbiConvertRate"}
    ],
    "dimensions": ["mediaName"],
    "filterConditions": [{
      "oper": "EQUAL",
      "source": "promotionTarget",
      "targetValue": ["问界M7"]
    }],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-07"
    }]
  }'
```

## 数据特性

1. **随机波动**: 所有指标值都包含随机波动,模拟真实数据
2. **比率约束**: 比率类指标(包含Rate, Bias, CVR等)保持在0-1之间
3. **因子约束**: 因子类指标(pacer, ratio等)在0.5-2之间
4. **维度组合**: 支持多维度交叉查询
5. **过滤支持**: 支持按维度值过滤数据
6. **小数精度**: 所有指标保留8位小数(与官方定义一致)

## 测试场景

mock-data.json 文件包含12个预定义测试场景:

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

## 配置文件说明

### dimensions.csv
定义所有37个官方维度,包括:
- dimension_code: 维度代码(对应官方name字段)
- dimension_name: 维度中文名称(对应官方dimensionName字段)
- dimension_en_name: 维度英文名称(对应官方dimensionEnName字段)
- dimension_desc: 维度描述(对应官方uxName字段)

### metrics.csv
定义所有56个官方指标,包括:
- metric_code: 指标代码(对应官方name字段)
- metric_name: 指标名称(对应官方uxName字段)
- indicator_id: 指标ID(对应官方indicatorId字段)
- decimal_places: 小数位数(对应官方decimalPlaces字段)

### dimension-values.csv
定义每个维度的示例值,包括:
- dimension_code: 维度代码
- value: 维度值
- value_desc: 值描述

## 环境变量

```bash
# 修改端口
PORT=8080 node mock/mock-server.js
```

## 注意事项

1. Mock 服务不需要认证,直接调用即可
2. 返回数据格式与真实 API 完全一致
3. 数据为随机生成,每次请求结果会有差异
4. 建议在开发和测试环境使用
5. 所有维度和指标均基于华为官方定义
6. 配置文件可由人工维护和扩充
