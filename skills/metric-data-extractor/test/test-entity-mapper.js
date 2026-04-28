const assert = require('assert');
const { EntityMapper } = require('../lib/entity-mapper');

const mapper = new EntityMapper();

const metric = mapper.mapMetric('消耗');
assert.strictEqual(metric.value, 'cost');

const dimension = mapper.mapDimension('推广对象');
assert.strictEqual(dimension.value, 'promotionTarget');

const mapped = mapper.map({
  metrics: ['消耗', '线索量'],
  dimensions: ['日期'],
  filters: {
    推广对象: '元保',
  },
});

assert.deepStrictEqual(mapped.metrics, ['cost', 'leads']);
assert.deepStrictEqual(mapped.dimensions, ['day']);
assert.deepStrictEqual(mapped.filters.promotionTarget.targetValue, ['yuanbao_insurance']);
assert.strictEqual(mapped.errors.length, 0);

const invalid = mapper.mapMetric('不存在的指标');
assert.strictEqual(invalid.error, true);

console.log('✓ 实体映射测试通过');
