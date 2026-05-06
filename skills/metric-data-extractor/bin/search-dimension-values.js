#!/usr/bin/env node
const { parseArgs, printJsonError } = require('../lib/arg-parser');
const { SemanticSearch } = require('../lib/semantic-search');

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const dimension = args.dimension;
  const query = args.query;
  const topK = args.topK ? Number.parseInt(args.topK, 10) : 5;

  if (!dimension || !query) {
    printJsonError('缺少必需参数', {
      usage: 'search-dimension-values --dimension <dimension_code> --query <search_text> [--top-k <number>]',
    });
    process.exit(1);
  }

  try {
    const searcher = new SemanticSearch();
    await searcher.initialize();
    const results = await searcher.search(dimension, query, Number.isNaN(topK) ? 5 : topK);

    console.log(
      JSON.stringify(
        {
          dimension,
          query,
          results,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    printJsonError(error.message);
    process.exit(1);
  }
}

main();
