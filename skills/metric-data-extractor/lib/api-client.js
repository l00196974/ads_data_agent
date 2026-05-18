const crypto = require('crypto');
const axios = require('axios');
const path = require('path');
const { readCsv } = require('./csv-loader');

const CACHE_FILE = path.join(__dirname, '..', '.wisedata-token-cache.json');

class HuaweiAdsClient {
  constructor({ appId, secret, baseUrl }) {
    this.appId = appId;
    this.secret = secret;
    this.baseUrl = baseUrl || 'https://wo-drcn.dbankcloud.cn';
    this.path = '/ads-data/openapi/v1/chart/common';
  }

  generateAuthHeader(body) {
    const timestamp = Date.now();
    const bodyString = JSON.stringify(body);
    const signString = `POST&${this.path}&&${bodyString}&appid=${this.appId}&timestamp=${timestamp}`;
    const signature = crypto.createHmac('sha256', this.secret).update(signString).digest('base64');

    return {
      authorization: `HMAC-SHA256 appid=${this.appId}, timestamp=${timestamp}, signature=${signature}`,
      timestamp,
    };
  }

  async query(requestBody) {
    const { authorization } = this.generateAuthHeader(requestBody);

    try {
      const headers = {
        'Content-Type': 'application/json',
      };

      if (!this.baseUrl.includes('localhost')) {
        headers['Authorization'] = authorization;
      }

      const response = await axios.post(`${this.baseUrl}${this.path}`, requestBody, {
        headers,
        timeout: 30000,
      });

      if (response.data.code !== 200) {
        throw new Error(`API错误: ${response.data.message || 'unknown error'}`);
      }

      return response;
    } catch (error) {
      if (error.response) {
        throw new Error(`API请求失败: ${error.response.status} - ${error.response.data.message || error.message}`);
      }

      if (error.request) {
        throw new Error('API请求超时或网络错误');
      }

      throw error;
    }
  }
}

/**
 * WisedataApiClient — 通过缓存 token 查询 v2/value 指标接口
 *
 * 将 DSLBuilder 生成的标准请求体转换为 wisedata v2/value 接口格式。
 * 认证凭据从 .wisedata-token-cache.json 加载（交互式登录脚本生成）。
 * 返回数据格式与 HuaweiAdsClient 保持一致。
 */
class WisedataApiClient {
  constructor({ baseUrl }) {
    this.baseUrl = baseUrl || 'https://wo-drcn.dbankcloud.cn';
    this.apiPath = '/ads-data/api/indicator/v2/value';
    this._cookie = null;
    this._csrfToken = null;

    // 加载映射表
    this._metricsMap = {};  // metric_code → {indicator_id, decimal_places}
    this._dimEnNameMap = {}; // dimension_code → dimension_en_name (snake_case)
    this._loadMappings();
  }

  _loadMappings() {
    // 加载指标映射：metric_code → indicator_id
    const metrics = readCsv('config/metrics.csv').filter(r => r.metric_code && r.indicator_id);
    for (const row of metrics) {
      this._metricsMap[row.metric_code] = {
        indicatorId: row.indicator_id,
        decimalPlaces: row.decimal_places !== undefined && row.decimal_places !== ''
  ? parseInt(row.decimal_places, 10)
  : 2,
      };
    }

    // 加载维度映射：dimension_code → dimension_en_name
    const dims = readCsv('config/dimensions.csv').filter(r => r.dimension_code && r.dimension_en_name);
    for (const row of dims) {
      this._dimEnNameMap[row.dimension_code] = row.dimension_en_name;
    }
  }

  /**
   * 确保认证有效 — 优先使用缓存 token
   */
  async ensureAuth() {
    if (this._cookie && this._csrfToken) return;

    const fs = require('fs');

    // 从缓存加载认证凭据 — 不需要 username，交互式登录后缓存已有 cookie + csrfToken
    try {
      if (fs.existsSync(CACHE_FILE)) {
        const data = JSON.parse(fs.readFileSync(CACHE_FILE, 'utf-8'));
        if (Date.now() < data.expiresAt) {
          this._cookie = data.cookie;
          this._csrfToken = data.csrfToken;
          return;
        }
      }
    } catch (_e) {}

    // 缓存不存在或已过期——先尝试静默刷新（后台有定期刷新任务兜底，这里再试一次保底）
    try {
      console.error('[WisedataApiClient.ensureAuth] 缓存过期，尝试静默刷新...');
      const { execSync } = require('child_process');
      execSync('node lib/refresh-wisedata-token.js', {
        cwd: path.join(__dirname, '..'),
        timeout: 130000,  // 略长于刷新脚本的 120s 超时
        stdio: ['ignore', 'ignore', 'pipe'],
      });
      // 刷新成功后重新读取缓存
      const data = JSON.parse(require('fs').readFileSync(CACHE_FILE, 'utf-8'));
      if (Date.now() < data.expiresAt) {
        this._cookie = data.cookie;
        this._csrfToken = data.csrfToken;
        console.error('[WisedataApiClient.ensureAuth] 静默刷新成功');
        return;
      }
    } catch (_refreshError) {
      // 静默刷新失败（SSO session 过期等），fall through 到交互式登录提示
    }

    // 静默刷新也失败了——指引用户重新交互式登录
    throw new Error(
      'Wisedata 登录缓存已过期，后台自动刷新也失败了（SSO session 可能已过期）。\n' +
      '请交互式登录重新生成缓存：\n' +
      '  node lib/wisedata-client.js <用户名>\n' +
      '（在 skills/metric-data-extractor/ 目录下执行）\n' +
      '完成浏览器登录后，重新执行查询即可。'
    );
  }

  /**
   * 将 DSLBuilder 生成的标准请求体转换为 v2/value API 格式
   *
   * DSLBuilder 输出示例：
   *   { dateTimeFilter: [{start: "2026-01-01", end: "2026-01-15"}],
   *     indicators: [{indicatorKey: "cost"}],
   *     timingDimension: "day",
   *     filterConditions: [{source, oper, targetValue}],
   *     dimensions: ["mediaName", ...],
   *     orderBy: [{source: "cost", order: "DESC"}],
   *     pageSize: ... }
   *
   * v2/value API 输出示例（camelCase）：
   *   { dateTimeFilter: {start: "epoch_ms", end: "epoch_ms"},
   *     indicators: [{id: "UUID", decimalPlaces: 2, original: false, timeFieldName: "pt_d"}],
   *     timingDimensions: ["day"],
   *     dimensions: ["media_name"],
   *     dimensionCondition: {oper, source, targetValue},
   *     orderBy: [{colName, type}],
   *     limit: ... }
   */
  _transformToValueRequest(body) {
    // dateTimeFilter: 数组 → {start, end} 毫秒时间戳（数字）
    const dtf = (body.dateTimeFilter || [])[0] || {};
    const dateTimeFilter = {
      start: String((() => {
        const s = String(dtf.start);
        return /^\d+$/.test(s) ? Number(s) : new Date(s + 'T00:00:00+08:00').getTime();
      })()),
      end: String((() => {
        const s = String(dtf.end);
        return /^\d+$/.test(s) ? Number(s) : new Date(s + 'T23:59:59.999+08:00').getTime();
      })()),
    };

    // indicators: indicatorKey → API 格式（timeFieldName 用 pt_d 代替 timestamp）
    const indicators = (body.indicators || []).map(indicator => {
      const mapping = this._metricsMap[indicator.indicatorKey];
      const id = mapping ? mapping.indicatorId : indicator.indicatorKey;
      console.error('[WisedataApiClient._transformToValueRequest] indicatorKey=%s → mapping=%j → id=%s',
        indicator.indicatorKey, mapping, id);
      return {
        id,
        decimalPlaces: mapping ? mapping.decimalPlaces : 2,
        original: false,
        timeFieldName: 'pt_d',
      };
    });

    // dimensions: dimension_code → dimension_en_name（API 使用英文名）
    const dimensions = (body.dimensions || []).map(d => this._dimEnNameMap[d] || d);

    // filterConditions → dimensionCondition / indicatorCondition
    // API 区分维度过滤和指标过滤两个独立参数：
    //   - dimensionCondition: source 是维度 code → 转 dim_en_name
    //   - indicatorCondition: source 是指标 code → 转 indicator_id（UUID）
    let dimensionCondition = undefined;
    let indicatorCondition = undefined;
    if (body.filterConditions && body.filterConditions.length > 0) {
      const dimChildren = [];
      const indChildren = [];
      for (const fc of body.filterConditions) {
        const metricMapping = this._metricsMap[fc.source];
        if (metricMapping) {
          // 指标过滤 → indicatorCondition
          indChildren.push({
            source: metricMapping.indicatorId,
            oper: fc.oper || 'EQUAL',
            targetValue: fc.targetValue || [],
          });
        } else {
          // 维度过滤 → dimensionCondition
          dimChildren.push({
            source: this._dimEnNameMap[fc.source] || fc.source,
            oper: fc.oper || 'EQUAL',
            targetValue: fc.targetValue || [],
          });
        }
      }
      if (dimChildren.length > 0) {
        dimensionCondition = { children: dimChildren, oper: 'AND' };
      }
      if (indChildren.length > 0) {
        indicatorCondition = { children: indChildren, oper: 'AND' };
      }
    }

    // orderBy: {colName, type} → Wisedata v2/value API 支持按维度和指标排序。
    // colName 传 dim_name（snake_case）或 indicator_id（UUID，即 _metricsMap 中的 indicatorId）。
    let orderBy = undefined;
    if (body.orderBy && body.orderBy.length > 0) {
      const ob = body.orderBy[0];
      if (ob.colName) {
        // 判断 colName 是否是指标 code → 转 indicator_id（UUID）
        const metricMapping = this._metricsMap[ob.colName];
        if (metricMapping) {
          orderBy = [{
            colName: metricMapping.indicatorId,
            type: String(ob.type || 'DESC').toUpperCase(),
          }];
        } else {
          // 按维度排序 → 做 en_name 映射
          orderBy = [{
            colName: this._dimEnNameMap[ob.colName] || ob.colName,
            type: String(ob.type || 'DESC').toUpperCase(),
          }];
        }
      }
    }

    const limit = Number(body.pageSize || body.limit || 1000);

    const result = {
      customGroupMap: {},
      dateTimeFilter,
      dimensionExtendedMap: {},
      indicators,
      limit,
      notFillDate: false,
      useCache: 0,
    };

    if (dimensions.length > 0) result.dimensions = dimensions;
    if (dimensionCondition) result.dimensionCondition = dimensionCondition;
    if (indicatorCondition) result.indicatorCondition = indicatorCondition;
    if (orderBy) result.orderBy = orderBy;
    if (body.timingDimension) {
      result.timingDimensions = Array.isArray(body.timingDimension) ? body.timingDimension : [body.timingDimension];
    }

    return result;
  }

  /**
   * 将 v2/value API 响应转换为标准行格式
   *
   * v2/value 响应结构：
   *   { data: [{ dimensionValues: {},
   *              indicatorValues: {"uuid": "value", ...},
   *              timingValues: {day: "2026-01-01", timestamp: "..."} }],
   *     count: N,
   *     dimensionNames: {},
   *     indicatorNames: {} }
   *
   * 转换为标准格式：
   *   [{ date: "2026-01-01", cost: "1000", cpm: "21.0", mediaName: "抖音", ... }]
   */
  _transformValueResponse(apiData) {
    const rows = apiData.data || [];
    if (!Array.isArray(rows) || rows.length === 0) {
      return { data: [], total: 0 };
    }

    // 反向映射：indicator_id → metric_code
    const idToCode = {};
    for (const [code, mapping] of Object.entries(this._metricsMap)) {
      idToCode[mapping.indicatorId] = code;
    }

    // 反向映射：dimension_en_name → dimension_code
    const enNameToCode = {};
    for (const [code, enName] of Object.entries(this._dimEnNameMap)) {
      enNameToCode[enName] = code;
    }

    const result = rows.map(row => {
      const entry = {};

      // 提取日期
      if (row.timingValues) {
        // 优先用 timingValues.day/week/month，其次用 timestamp
        const timeKey = Object.keys(row.timingValues).find(k => k !== 'timestamp');
        entry.date = timeKey ? row.timingValues[timeKey] : null;
        if (!entry.date && row.timingValues.timestamp) {
          // 从 timestamp 转日期
          const d = new Date(parseInt(row.timingValues.timestamp, 10));
          entry.date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        }
      }

      // 展开维度值（用 dimension_code 作为 key）
      if (row.dimensionValues) {
        for (const [enName, value] of Object.entries(row.dimensionValues)) {
          entry[enNameToCode[enName] || enName] = value;
        }
      }

      // 展开指标值（用 metric_code 作为 key）
      if (row.indicatorValues) {
        for (const [indicatorId, value] of Object.entries(row.indicatorValues)) {
          entry[idToCode[indicatorId] || indicatorId] = value;
        }
      }

      return entry;
    });

    // 按 date 排序
    result.sort((a, b) => (a.date || '').localeCompare(b.date || ''));

    return { data: result, total: apiData.count || result.length };
  }

  async query(requestBody) {
    await this.ensureAuth();

    const valueBody = this._transformToValueRequest(requestBody);

    console.error('[WisedataApiClient.query] 发送到 v2/value API 的请求体: %s', JSON.stringify(valueBody, null, 2));

    try {
      const response = await axios.post(
        `${this.baseUrl}${this.apiPath}`,
        valueBody,
        {
          headers: {
            'Content-Type': 'application/json; charset=UTF-8',
            'Accept': 'application/json, text/plain, */*',
            'Cookie': this._cookie,
            'x-csrf-token': this._csrfToken,
            'origin': this.baseUrl,
            'referer': `${this.baseUrl}/ads-data/`,
            'x-portal-site': 'portal',
            'x-lang': 'zh',
          },
          timeout: 60000,
        }
      );

      if (response.data.code !== 200) {
        // 把请求上下文拼进错误消息，方便追踪原因
        const reqCtx = {
          indicators: valueBody.indicators?.map(i => i.id).join(',') || '-',
          dimensions: valueBody.dimensions?.join(',') || '-',
          orderBy: valueBody.orderBy ? `${valueBody.orderBy[0].colName} ${valueBody.orderBy[0].type}` : '-',
          hasDimensionCondition: !!valueBody.dimensionCondition,
          hasIndicatorCondition: !!valueBody.indicatorCondition,
        };
        throw new Error(
          `API错误: ${response.data.message || 'unknown error'} ` +
          `[indicators=${reqCtx.indicators} dims=${reqCtx.dimensions} ` +
          `orderBy=${reqCtx.orderBy} dimCond=${reqCtx.hasDimensionCondition} indCond=${reqCtx.hasIndicatorCondition}]`
        );
      }

      // 将 v2/value 响应转换为标准行格式
      const transformedData = this._transformValueResponse(response.data.data);

      // 构造与 HuaweiAdsClient.query() 一致的返回结构
      return {
        data: {
          code: 200,
          data: transformedData,
          message: 'OK',
        },
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      };
    } catch (error) {
      if (error.response) {
        if (error.response.status === 401 || error.response.status === 403) {
          this._cookie = null;
          this._csrfToken = null;
        }
        throw new Error(`API请求失败: ${error.response.status} - ${error.response.data?.message || error.message}`);
      }

      if (error.request) {
        throw new Error('API请求超时或网络错误');
      }

      throw error;
    }
  }
}

module.exports = {
  HuaweiAdsClient,
  WisedataApiClient,
};
