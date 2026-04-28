# metric-data-extractor

OpenClaw Skill，用于统一查询华为广告指标数据，支持：

- 指标与维度的 CSV 配置化管理
- 中文别名到 API 字段的实体对齐
- 维度值语义检索与自动修复
- HMAC-SHA256 请求签名
- Mock 服务本地调试

## 安装

### 1. 安装依赖

Linux / macOS：

```bash
npm install
```

Windows PowerShell：

```powershell
npm install
```

### 2. 全局安装命令（可选）

如果想在任何目录直接使用 `query-metrics` 和 `search-dimension-values` 命令：

Linux / macOS：

```bash
npm link
```

Windows PowerShell（需要管理员权限）：

```powershell
npm link
```

安装后可以直接使用：
```bash
query-metrics --help
search-dimension-values --help
```

如果不执行 `npm link`，需要使用完整路径：
```bash
node bin/query-metrics.js --help
```

## 配置文件说明

本项目包含3个核心配置文件（位于 `config/` 目录）：

**首次使用前，请复制示例文件：**
```bash
cd config/
cp metrics.csv.example metrics.csv
cp dimensions.csv.example dimensions.csv
cp dimension-values.csv.example dimension-values.csv
```

**注意**：真实配置文件（`*.csv`）已加入 `.gitignore`，不会被提交到 Git，避免覆盖你的本地配置。

### metrics.csv（指标配置）
包含官方指标，每个指标包含：
- `metric_code`: 指标编码（如 click, pbiConvertRate）
- `metric_name`: 指标名称（如 点击量, 转化率）
- `metric_desc`: 指标详细描述
- `category_level1`: 一级分类（基础指标、竞价指标、填充指标等）
- `category_level2`: 二级分类（点击相关、召回相关等）
- `supported_dimensions`: 支持的维度列表
- `metric_aliases`: 指标别名（支持多种说法）
- `indicator_id`: 官方指标ID
- `decimal_places`: 小数位数

### dimensions.csv（维度配置）
包含37个官方维度，每个维度包含：
- `dimension_code`: 维度编码（如 reqDay, priceType）
- `dimension_name`: 维度名称（如 请求时间, 计费方式）
- `dimension_en_name`: 维度英文名
- `dimension_desc`: 维度详细说明
- `dimension_aliases`: 维度别名（支持多种说法）
- `value_examples`: 维度值示意（枚举值或示例）
- `dimension_type`: 维度类型（enum/semi-enum/string/id/date/time/package）

### dimension-values.csv（维度值配置）
为每个维度提供具体的可选值，用于校验外部模型输入。

详细的配置说明请参考 [CHANGELOG.md](CHANGELOG.md)。

## 可用指标和维度

### 常用指标示例
- **基础指标**: click（点击量）, exposure（曝光）
- **转化指标**: cvr（转化率）
- **成本指标**: cost（流水）, cpm（CPM）, ecpm（eCPM）
- **算法指标**: pctrBias（pctr bias）, pcvrBias（pcvr bias）

### 常用维度示例
- **时间维度**: reqDay（请求时间）, reqHour（请求小时）
- **业务维度**: promotionTarget（推广标的）, priceType（计费方式）, mediaName（媒体名称）
- **任务维度**: adGroupName（任务名称）, adGroupStatus（任务状态）

完整列表请查看 `config/metrics.csv` 和 `config/dimensions.csv` 文件。

## 本地调试

启动 Mock 服务：

Linux / macOS：

```bash
npm run mock
```

Windows PowerShell：

```powershell
npm run mock
```

查询示例：

Linux / macOS：

```bash
# 使用全局命令（需要先执行 npm link）
query-metrics \
  --metrics "click,cvr" \
  --start-date "2026-01-01" \
  --end-date "2026-01-07" \
  --time-mode event \
  --dimensions "day,priceType" \
  --mock

# 或使用完整路径
node bin/query-metrics.js \
  --metrics "click,cvr" \
  --start-date "2026-01-01" \
  --end-date "2026-01-07" \
  --time-mode event \
  --dimensions "day,priceType" \
  --mock
```

Windows PowerShell（建议使用单行命令，避免续行符导致参数解析错误）：

```powershell
# 使用全局命令（需要先执行 npm link）
query-metrics --metrics "click,cvr" --start-date "2026-01-01" --end-date "2026-01-07" --time-mode event --dimensions "day,priceType" --mock

# 或使用完整路径
node .\bin\query-metrics.js --metrics "click,cvr" --start-date "2026-01-01" --end-date "2026-01-07" --time-mode event --dimensions "day,priceType" --mock
```

### --filters 参数传 JSON

格式：`{"维度代码": ["值1", "值2"]}`

**用单引号包裹 JSON，Linux bash 和 Windows PowerShell 写法完全相同**：

```
query-metrics --metrics "click,cvr" --start-date "2026-01-01" --end-date "2026-01-07" --time-mode event --dimensions "day,priceType" --filters '{"promotionTarget": ["问界M7车型"]}' --mock
```

多个条件：

```
query-metrics --metrics "click,cvr" --start-date "2026-01-01" --end-date "2026-01-07" --time-mode event --dimensions "day,priceType" --filters '{"promotionTarget": ["问界M7车型", "元保保险"], "priceType": ["CPC", "CPM"]}' --mock
```

> 注意：不要用双引号包裹 JSON（`"{ ... }"`），在 Windows PowerShell 下需要对内层双引号转义（`\"`），容易出错。

**Mock数据说明：**
- 时间范围：2026-03-01 到 2026-03-07
- 支持的推广对象：问界M7、元保保险、某电商APP、某教育APP
- 支持两种时间口径：event（事件发生时间）和 request（广告请求时间）

**注意**: 示例中使用的指标必须存在于 `config/metrics.csv` 中。如果指标不存在，会返回错误并提供建议。

语义检索示例：

Linux / macOS：

```bash
# 使用全局命令
search-dimension-values --dimension "promotionTarget" --query "保险产品"

# 或使用完整路径
node bin/search-dimension-values.js --dimension "promotionTarget" --query "保险产品"
```

Windows PowerShell：

```powershell
# 使用全局命令
search-dimension-values --dimension "promotionTarget" --query "保险产品"

# 或使用完整路径
node .\bin\search-dimension-values.js --dimension "promotionTarget" --query "保险产品"
```

## 测试

Linux / macOS：

```bash
npm test
```

Windows PowerShell：

```powershell
npm test
```

## 常见问题

### 1. 命令找不到：`query-metrics: command not found`

**原因**: 没有执行全局安装。

**解决方案**:
```bash
# 方案1: 全局安装命令
npm link

# 方案2: 使用完整路径
node bin/query-metrics.js --help
```

### 2. 指标无法识别

**错误示例**:
```json
{
  "error": "指标 \"消耗\" 无法识别"
}
```

**原因**: 指标不存在于 `config/metrics.csv` 中。

**解决方案**:
- 查看 `config/metrics.csv` 获取完整的指标列表
- 使用实际存在的指标，如：`点击量`、`转化率`、`实收点击流水` 等

### 3. Mock服务端口被占用

**错误**: `Error: listen EADDRINUSE: address already in use :::3000`

**解决方案**:
```bash
# 查找并杀死占用3000端口的进程
lsof -ti:3000 | xargs kill -9

# 或使用其他端口
PORT=3001 npm run mock
```

### 4. Windows路径问题

**错误**: 在Windows上使用 `.\bin\query-metrics.js` 报错

**解决方案**:
- 使用 PowerShell 而不是 CMD
- 或使用全局命令（执行 `npm link` 后）

### 5. Windows PowerShell 下 --filters JSON 解析失败

**错误**: 传入 `--filters` 后报 `JSON parse error` 或参数被截断。

**原因**: PowerShell 不支持用单引号包裹 JSON（`'{"key": "val"}'`），内层双引号会被吞掉。

**解决方案**: 使用外层双引号 + 内层 `\"` 转义：

```powershell
# 错误写法（Linux 单引号风格，PowerShell 不支持）
--filters '{"promotionTarget": ["问界M7车型"]}'

# 正确写法（PowerShell）
--filters "{\"promotionTarget\": [\"问界M7车型\"]}"
```

**OpenClaw 用户**：OpenClaw 在 Windows 下通过 PowerShell 执行命令，必须使用上述 PowerShell 转义写法。

## 更新日志

详细的更新历史请查看 [CHANGELOG.md](CHANGELOG.md)。
