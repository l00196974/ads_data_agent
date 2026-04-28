#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { parse } = require('csv-parse/sync');

function main() {
  try {
    const metricsPath = path.resolve(__dirname, '../config/metrics.csv');
    const content = fs.readFileSync(metricsPath, 'utf-8');
    const records = parse(content, {
      columns: true,
      skip_empty_lines: true,
    });

    const result = {
      total: records.length,
      metrics: records.map((r) => ({
        code: r.metric_code,
        name: r.metric_name,
        description: r.metric_desc,
      })),
    };

    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(JSON.stringify({ error: error.message }, null, 2));
    process.exit(1);
  }
}

main();
