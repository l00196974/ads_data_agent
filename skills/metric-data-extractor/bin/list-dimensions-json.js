#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { parse } = require('csv-parse/sync');

function main() {
  try {
    const dimensionsPath = path.resolve(__dirname, '../config/dimensions.csv');
    const content = fs.readFileSync(dimensionsPath, 'utf-8');
    const records = parse(content, {
      columns: true,
      skip_empty_lines: true,
    });

    const result = {
      total: records.length,
      dimensions: records.map((r) => ({
        code: r.dimension_code,
        name: r.dimension_name,
        description: r.dimension_desc,
      })),
    };

    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(JSON.stringify({ error: error.message }, null, 2));
    process.exit(1);
  }
}

main();
