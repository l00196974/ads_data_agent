const assert = require('assert');
const { SemanticSearch } = require('../lib/semantic-search');

async function main() {
  const searcher = new SemanticSearch();
  await searcher.initialize();

  const results = await searcher.search('promotionTarget', '保险产品', 3);
  assert.ok(results.length > 0);
  assert.strictEqual(results[0].value_code, 'yuanbao_insurance');

  console.log('✓ 语义检索测试通过');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
