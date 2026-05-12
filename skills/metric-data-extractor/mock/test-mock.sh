#!/bin/bash

# Mock 服务测试脚本 - 基于官方维度和指标

BASE_URL="http://localhost:3000/ads-data/openapi/v1/chart/common"

echo "=== 测试1: 基础效果分析 - 点击、曝光、转化 ==="
curl -X POST $BASE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [
      {"indicatorKey": "click"},
      {"indicatorKey": "receivedExposure"},
      {"indicatorKey": "pbiConvertRate"}
    ],
    "dimensions": ["reqDay"],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-05"
    }]
  }' 2>/dev/null | python3 -m json.tool

echo -e "\n\n=== 测试2: 竞价分析 - 召回、粗排、精排 ==="
curl -X POST $BASE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [
      {"indicatorKey": "recallSumCount"},
      {"indicatorKey": "roughRowParticipation"},
      {"indicatorKey": "preciseRowParticipation"},
      {"indicatorKey": "roughRowWinRate"},
      {"indicatorKey": "preciseRowWinRate"}
    ],
    "dimensions": ["priceType"],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-03"
    }]
  }' 2>/dev/null | python3 -m json.tool

echo -e "\n\n=== 测试3: 成本分析 - 转化成本、流水 ==="
curl -X POST $BASE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [
      {"indicatorKey": "realityConversionCost"},
      {"indicatorKey": "actualSpent"},
      {"indicatorKey": "clickActualSpent"},
      {"indicatorKey": "corpValue"}
    ],
    "dimensions": ["adGroupName"],
    "filterConditions": [{
      "oper": "EQUAL",
      "source": "adGroupStatus",
      "targetValue": ["运行中"]
    }],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-05"
    }]
  }' 2>/dev/null | python3 -m json.tool

echo -e "\n\n=== 测试4: 填充分析 - 库存、填充率 ==="
curl -X POST $BASE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [
      {"indicatorKey": "stockCount"},
      {"indicatorKey": "adsRequestCount"},
      {"indicatorKey": "adReturnCount"},
      {"indicatorKey": "adFillRateCount"}
    ],
    "dimensions": ["mediaName", "positionName"],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-03"
    }]
  }' 2>/dev/null | python3 -m json.tool

echo -e "\n\n=== 测试5: 深层转化分析 ==="
curl -X POST $BASE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [
      {"indicatorKey": "adGroupShallowConversionNumber"},
      {"indicatorKey": "adGroupDeepConversionNumber"},
      {"indicatorKey": "deepCvr"},
      {"indicatorKey": "deepCtcvr"}
    ],
    "dimensions": ["deepEffectType"],
    "filterConditions": [{
      "oper": "EQUAL",
      "source": "promotionTarget",
      "targetValue": ["问界M7"]
    }],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-05"
    }]
  }' 2>/dev/null | python3 -m json.tool

echo -e "\n\n=== 测试6: 价格分析 - ECPM、一二价 ==="
curl -X POST $BASE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [
      {"indicatorKey": "realEcpm"},
      {"indicatorKey": "dspRealEcpm"},
      {"indicatorKey": "clickFirstPrice"},
      {"indicatorKey": "clickSecondPrice"},
      {"indicatorKey": "firstSecondPriceGap"}
    ],
    "dimensions": ["reqDay", "priceType"],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-03"
    }]
  }' 2>/dev/null | python3 -m json.tool

echo -e "\n\n=== 测试7: 算法分析 - Bias指标 ==="
curl -X POST $BASE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [
      {"indicatorKey": "pctrBias"},
      {"indicatorKey": "pcvrBias"},
      {"indicatorKey": "finalPCVRBias"},
      {"indicatorKey": "resPcvr"}
    ],
    "dimensions": ["algModeTag"],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-03"
    }]
  }' 2>/dev/null | python3 -m json.tool

echo -e "\n\n=== 测试8: UV/PV分析 ==="
curl -X POST $BASE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "indicators": [
      {"indicatorKey": "dspReqUV"},
      {"indicatorKey": "dspReqPV"},
      {"indicatorKey": "activeUV"}
    ],
    "dimensions": ["reqDay"],
    "dateTimeFilter": [{
      "start": "2026-03-01",
      "end": "2026-03-05"
    }]
  }' 2>/dev/null | python3 -m json.tool

echo -e "\n\n所有测试完成!"
