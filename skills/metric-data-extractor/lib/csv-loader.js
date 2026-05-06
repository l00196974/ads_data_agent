const fs = require('fs');
const path = require('path');
const Papa = require('papaparse');

/**
 * 读取 skill 包内的 CSV 文件并解析为对象数组（首行为表头）。
 *
 * - 自动剥 UTF-8 BOM（Notepad++ / Excel 默认带 BOM 保存，不剥会让首列名变成
 *   `﻿col_name`，下游 mapper 字段查找全部失败）。
 * - 文件缺失时抛带 hint 的错误指向 *.example —— SKILL.md 已告诉用户要从
 *   .example 拷贝，把这个 hint 在错误最近的位置再说一遍。
 *
 * 这是所有 lib/ 模块加载 CSV 的唯一入口。bin/ 下的 list-metrics /
 * list-dimensions 也复用，避免两条路径行为不一致。
 */
function readCsv(relativePath) {
  const absolutePath = path.join(__dirname, '..', relativePath);
  let content;
  try {
    content = fs.readFileSync(absolutePath, 'utf8');
  } catch (err) {
    if (err.code === 'ENOENT') {
      throw new Error(
        `CSV 文件缺失: ${relativePath}\n` +
        `请从 ${relativePath}.example 复制：cp ${relativePath}.example ${relativePath}\n` +
        `（部署时业务数据需要单独配置，不入仓库）`
      );
    }
    throw err;
  }
  // 剥 UTF-8 BOM
  if (content.charCodeAt(0) === 0xFEFF) {
    content = content.slice(1);
  }
  const parsed = Papa.parse(content, {
    header: true,
    skipEmptyLines: true,
  });
  return parsed.data;
}

module.exports = {
  readCsv,
};
