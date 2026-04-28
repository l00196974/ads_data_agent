#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { parse } = require('csv-parse/sync');
const { parseArgs } = require('../lib/arg-parser');

function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const format = args.format || 'table'; // 默认表格格式，支持 'json' 格式

    const dimensionsPath = path.resolve(__dirname, '../config/dimensions.csv');
    let content = fs.readFileSync(dimensionsPath, 'utf-8');

    // 移除BOM字符（如果存在）
    if (content.charCodeAt(0) === 0xFEFF) {
      content = content.slice(1);
    }

    const records = parse(content, {
      columns: true,
      skip_empty_lines: true,
    });

    if (format === 'json') {
      // JSON格式输出（用于Agent系统）
      const result = {
        total: records.length,
        dimensions: records.map((r) => ({
          code: r.dimension_code,
          name: r.dimension_name,
          description: r.dimension_desc,
          aliases: r.dimension_aliases ? r.dimension_aliases.split(',').map(s => s.trim()) : [],
          type: r.dimension_type || 'string',
          examples: r.value_examples || ''
        })),
      };
      console.log(JSON.stringify(result, null, 2));
    } else {
      // 表格格式输出（用于命令行）
      console.log(`## 可用维度列表 (共${records.length}个)`);
      console.log('');
      console.log('| 维度代码 | 维度名称 | 类型 | 说明 | 别名 |');
      console.log('|---------|---------|------|------|------|');

      records.forEach((r) => {
        const aliases = r.dimension_aliases || '';
        const type = r.dimension_type || 'string';
        console.log(`| ${r.dimension_code} | ${r.dimension_name} | ${type} | ${r.dimension_desc || ''} | ${aliases} |`);
      });
    }

  } catch (error) {
    if (args && args.format === 'json') {
      console.error(JSON.stringify({ error: error.message }, null, 2));
    } else {
      console.error(`错误: ${error.message}`);
    }
    process.exit(1);
  }
}

main();
