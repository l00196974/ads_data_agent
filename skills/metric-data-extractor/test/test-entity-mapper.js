const assert = require('assert');
const { EntityMapper } = require('../lib/entity-mapper');

const mapper = new EntityMapper();

const metric = mapper.mapMetric('消耗');
assert.strictEqual(metric.value, 'cost');

const dimension = mapper.mapDimension('推广对象');
assert.strictEqual(dimension.value, 'promotionTarget');

// 用 metrics.csv 中**稳定存在**的指标做断言——别用"线索量"这种业务演进后可能消失的
// 别名（早期 metrics.csv 含 leads，后来移除导致此处一直 fail 但被忽视）。
// '消耗' → cost、'点击量' → click 都是 metrics.csv 永久存在的基础指标，演进不易破。
const mapped = mapper.map({
  metrics: ['消耗', '点击量'],
  dimensions: ['日期'],
  filters: {
    推广对象: '元保',
  },
});

assert.deepStrictEqual(mapped.metrics, ['cost', 'click']);
assert.deepStrictEqual(mapped.dimensions, ['pt_d']);
// promotionTarget 是 string 类型维度（自由文本，dimension_type=string），
// mapper 不做 value 别名映射——直接透传原值。
// （若需要"元保 → 元保保险" 这种近似词归一化，要走 search-dimension-values）
assert.deepStrictEqual(mapped.filters.promotionTarget.targetValue, ['元保']);
assert.strictEqual(mapped.errors.length, 0);

const invalid = mapper.mapMetric('不存在的指标');
assert.strictEqual(invalid.error, true);

console.log('✓ 实体映射测试通过');
