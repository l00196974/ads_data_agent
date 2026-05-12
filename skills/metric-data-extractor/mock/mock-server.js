const express = require('express');

const app = express();
app.use(express.json());

function enumerateDates(startDate, endDate) {
  const dates = [];
  const cursor = new Date(startDate);
  const end = new Date(endDate);

  while (cursor <= end) {
    dates.push(cursor.toISOString().split('T')[0]);
    cursor.setDate(cursor.getDate() + 1);
  }

  return dates;
}

function dimensionValuesFor(code) {
  const dimensionMap = {
    // 根据新的dimensions.csv更新
    operatingIndustryLevel1NotConsistent: ['汽车', '保险', '电商', '教育', '游戏', '金融'],
    operatingIndustryLevel2NotConsistent: ['新能源汽车', '传统汽车', '车险', '寿险'],
    promotionTarget: ['问界M7车型', '元保保险', '某电商APP'],
    positionName: ['信息流版位', '搜索版位', '开屏版位', '详情页版位'],
    slotName: ['首页信息流', '搜索结果页'],
    dspId: ['1', '2', '3'],
    promoteAppName: ['问界汽车', '元保保险'],
    positionId: ['position_001', 'position_002'],
    slotId: ['slot_001', 'slot_002'],
    priceType: ['CPC', 'CPM', 'CPA', 'oCPC', 'oCPM'],
    adGroupName: ['问界M7推广任务01', '元保车险推广任务01'],
    mediaName: ['华为浏览器', '华为视频', '华为音乐', '华为阅读', '华为应用市场'],
    adGroupStatus: ['运行中', '已暂停', '已结束', '待审核'],
    corpId: ['corp_001', 'corp_002'],
    mediaType: ['视频', '资讯', '音乐', '阅读', '游戏', '浏览器', '应用市场'],
    slotForm: ['信息流', '横幅', '插屏', '开屏', '激励视频'],
    corpName: ['问界汽车广告主', '元保保险广告主'],
    adGroupId: ['adgroup_001', 'adgroup_002'],
    dspName: ['DSP_A', 'DSP_B'],
    shallowEffectType: ['下载', '注册', '表单提交', '咨询', '激活'],
    deepEffectType: ['付费', '次留', '购买', '激活', '关键行为'],
    reqDay: ['2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04', '2026-03-05'],
    day: ['2026/1/1', '2026/1/2', '2026/1/3'],
    algModeTag: ['oCPC', 'oCPM', '智能出价'],
    appFirstIndustryClass: ['视频', '资讯', '音乐', '阅读', '游戏'],
    appSecondIndustryClass: ['短视频', '长视频', '新闻资讯', '小说阅读'],
    sspName: ['SSP_A', 'SSP_B'],
    campaignId: ['campaign_001', 'campaign_002'],
    promoteAppPkg: ['com.huawei.aito', 'com.example.app'],
    mediaAppName: ['华为浏览器', '华为视频'],
    appSecondClassName: ['AG二级分类示例'],
    packageName: ['com.huawei.browser', 'com.huawei.himovie'],
    mediaAppSecondClass: ['短视频', '长视频', '新闻资讯', '小说阅读'],
    promotionType: ['PDB', 'PD', 'RTB'],
  };

  return dimensionMap[code] || [];
}

// 简单确定性哈希，用日期index和指标名生成固定扰动
function deterministicOffset(indicatorKey, index) {
  let hash = index * 2654435761;
  for (let i = 0; i < indicatorKey.length; i++) {
    hash = (hash ^ indicatorKey.charCodeAt(i)) * 2654435761;
  }
  // 归一化到 [-1, 1]
  return ((hash >>> 0) % 10000) / 10000 * 2 - 1;
}

function baseValue(indicatorKey, index) {
  const seed = {
    // 官方指标 - 基础指标
    click: 24000,
    receivedExposure: 900000,

    // 竞价相关
    recallSumCount: 1500000,
    preciseRowParticipation: 1200000,
    roughRowParticipation: 1300000,
    roughRowWinRate: 0.85,
    preciseRowWinRate: 0.75,
    preciseRankWinRate: 0.75,
    impreciseRankWinRate: 0.85,
    recallSumWinRate: 0.65,

    // 填充相关
    adReturnNumber: 950000,
    mediaReceiveCount: 950000,
    dspReturnCount: 920000,
    stockCount: 1200000,
    stockNumber: 1100000,
    adsRequestCount: 1300000,
    adRequestNumber: 1250000,
    adReturnCount: 980000,

    // 转化相关
    pbiConvertRate: 0.11,
    adGroupShallowConversionNumber: 1800,
    adGroupDeepConversionNumber: 800,
    deepCvr: 0.033,
    deepCtcvr: 0.0089,

    // 成本相关
    realityConversionCost: 250,
    shallowCostOffset: 0.92,
    deepCostOffset: 0.88,
    deepExceptConversionCost: 380,

    // 价格相关
    realEcpm: 520,
    dspRealEcpm: 480,
    clickFirstPrice: 22.5,
    clickSecondPrice: 18.3,
    firstSecondPriceGap: 4.2,
    clickFirstSecondPriceGap: 4.2,

    // 流水相关
    actualSpent: 450000,
    clickActualSpent: 420000,
    revenueGap: 30000,
    corpValue: 580000,

    // 算法相关
    pctrBias: 0.015,
    pcvrBias: 0.022,
    finalPCVRBias: 0.018,
    resPcvr: 0.095,
    resPcvrBias: 0.012,

    // 出价相关
    pacer1: 1.15,
    costRatio: 0.95,
    costRatioCnt: 18500,
    feeDeductionRatio: 0.88,

    // 填充率相关
    adFillRateCount: 0.75,
    adFillRateNumber: 0.78,
    adImpRateCount: 0.92,
    adImpRateNumber: 0.94,

    // UV/PV
    dspReqUV: 450000,
    dspReqPV: 1200000,
    activeUV: 2500000,

    // RTA
    rtaBidRate: 0.82,

    // 其他
    totalOcpxAdvTargetCnvrPrice: 280,
  };

  const value = seed[indicatorKey] || 100;

  // 比率类指标保持在0-1之间 (包含Rate, Bias, CVR, CTR等)
  const isRatio = indicatorKey.toLowerCase().includes('rate') ||
                  indicatorKey.toLowerCase().includes('bias') ||
                  indicatorKey.toLowerCase().includes('cvr') ||
                  indicatorKey.toLowerCase().includes('ctr') ||
                  indicatorKey.toLowerCase().includes('ratio') && !indicatorKey.includes('Cnt');

  if (isRatio) {
    const variation = deterministicOffset(indicatorKey, index) * 0.01;
    return Number(Math.max(0, Math.min(1, value + variation)).toFixed(8));
  }

  // 因子类指标 (pacer, ratio等) 在0.5-2之间
  if (['pacer1', 'costRatio', 'feeDeductionRatio'].includes(indicatorKey)) {
    const variation = deterministicOffset(indicatorKey, index) * 0.15;
    return Number(Math.max(0.5, Math.min(2, value + variation)).toFixed(8));
  }

  // 价格/成本类指标 (包含Price, Cost, Ecpm等)
  const isPrice = indicatorKey.toLowerCase().includes('price') ||
                  indicatorKey.toLowerCase().includes('cost') ||
                  indicatorKey.toLowerCase().includes('ecpm') ||
                  indicatorKey.toLowerCase().includes('spent') ||
                  indicatorKey.toLowerCase().includes('value') ||
                  indicatorKey.toLowerCase().includes('gap');

  if (isPrice) {
    const variation = deterministicOffset(indicatorKey, index) * value * 0.1;
    return Number((value + variation).toFixed(8));
  }

  // 计数类指标 (包含Count, Number, UV, PV等)
  const isCount = indicatorKey.toLowerCase().includes('count') ||
                  indicatorKey.toLowerCase().includes('number') ||
                  indicatorKey.toLowerCase().includes('uv') ||
                  indicatorKey.toLowerCase().includes('pv') ||
                  indicatorKey.toLowerCase().includes('click') ||
                  indicatorKey.toLowerCase().includes('exposure');

  if (isCount) {
    const variation = deterministicOffset(indicatorKey, index) * value * 0.15;
    return Number(Math.max(0, value + variation + index * 137.5).toFixed(0));
  }

  // 默认数量类指标
  const variation = deterministicOffset(indicatorKey, index) * value * 0.15;
  return Number(Math.max(0, value + variation + index * 137.5).toFixed(8));
}

app.post('/ads-data/openapi/v1/chart/common', (req, res) => {
  const { indicators = [], dimensions = [], dateTimeFilter = [], filterConditions = [] } = req.body || {};
  const startDate = dateTimeFilter[0]?.start || '2026-01-01';
  const endDate = dateTimeFilter[0]?.end || startDate;
  const dates = enumerateDates(startDate, endDate);

  let rows = dates.map((date, index) => {
    const row = { date };
    for (const indicator of indicators) {
      row[indicator.indicatorKey] = baseValue(indicator.indicatorKey, index);
    }
    return row;
  });

  for (const dimension of dimensions || []) {
    if (dimension === 'day') {
      continue;
    }

    // 特殊处理 reqDay 维度 - 使用日期范围内的日期
    if (dimension === 'reqDay') {
      rows = rows.flatMap((row, rowIndex) =>
        dates.map((date, dateIndex) => {
          const next = { ...row, reqDay: date };
          // 为 reqDay 维度添加轻微的数据变化
          for (const indicator of indicators) {
            const current = next[indicator.indicatorKey];
            if (typeof current === 'number') {
              const variation = (dateIndex + 1) * 0.05; // 5% 的日期相关变化
              next[indicator.indicatorKey] = Number((current * (1 + variation)).toFixed(4));
            }
          }
          return next;
        }),
      );
      continue;
    }

    const values = dimensionValuesFor(dimension);
    if (values.length === 0) {
      continue;
    }

    rows = rows.flatMap((row, rowIndex) =>
      values.map((value, valueIndex) => {
        const factor = valueIndex + 1;
        const next = { ...row, [dimension]: value };
        for (const indicator of indicators) {
          const current = next[indicator.indicatorKey];
          if (typeof current === 'number') {
            next[indicator.indicatorKey] = Number((current / factor + rowIndex * 17).toFixed(4));
          }
        }
        return next;
      }),
    );
  }

  for (const filter of filterConditions) {
    rows = rows.filter((row) => {
      if (!(filter.source in row)) {
        return true;
      }

      // 特殊处理 reqDay 字段的日期过滤
      if (filter.source === 'reqDay') {
        const rowDate = row[filter.source];
        const targetDates = filter.targetValue || [];

        // 支持日期范围过滤
        if (targetDates.length === 2 && targetDates[0].includes('~')) {
          const [startDate, endDate] = targetDates[0].split('~');
          return rowDate >= startDate && rowDate <= endDate;
        }

        // 支持具体日期列表过滤
        return targetDates.includes(rowDate);
      }

      return (filter.targetValue || []).includes(row[filter.source]);
    });
  }

  res.json({
    code: 200,
    data: {
      data: rows,
      total: rows.length,
    },
    message: 'OK',
  });
});

const port = Number(process.env.PORT || 3000);
app.listen(port, () => {
  console.log(`Mock API服务启动: http://localhost:${port}`);
});
