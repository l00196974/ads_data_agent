const { readCsv } = require('./csv-loader');

function tokenize(text) {
  return String(text || '')
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .map((item) => item.trim())
    .filter(Boolean);
}

function unique(items) {
  return Array.from(new Set(items));
}

class SemanticSearch {
  constructor() {
    this.dimensionValuesCache = [];
    this.initialized = false;
    this.useTransformers = false;
    this.pipeline = null;
  }

  async initialize() {
    if (this.initialized) {
      return;
    }

    this.dimensionValuesCache = readCsv('config/dimension-values.csv').filter((row) => row.dimension_code);

    try {
      const transformers = require('@xenova/transformers');
      this.pipeline = await transformers.pipeline(
        'feature-extraction',
        'Xenova/paraphrase-multilingual-MiniLM-L12-v2',
      );
      this.useTransformers = true;
    } catch (_error) {
      this.useTransformers = false;
    }

    this.initialized = true;
  }

  buildSearchText(row) {
    // value_aliases 是逗号分隔的别名串（如 "HUAWEI Browser,华为浏览器APP"），
    // 拼进搜索文本让 lexical / 语义匹配都能命中别名——之前只搜 value+desc，
    // 用户输入"AG"找"华为应用市场"会失败。
    return [row.value, row.value_desc, row.value_aliases]
      .filter(Boolean)
      .join(' ');
  }

  lexicalScore(query, text) {
    const queryTokens = unique(tokenize(query));
    const textTokens = unique(tokenize(text));

    if (queryTokens.length === 0 || textTokens.length === 0) {
      return 0;
    }

    let hits = 0;
    for (const token of queryTokens) {
      if (textTokens.some((textToken) => textToken.includes(token) || token.includes(textToken))) {
        hits += 1;
      }
    }

    return hits / queryTokens.length;
  }

  async vectorScore(query, text) {
    if (!this.useTransformers || !this.pipeline) {
      return 0;
    }

    const [queryEmbedding, textEmbedding] = await Promise.all([
      this.getEmbedding(query),
      this.getEmbedding(text),
    ]);

    return this.cosineSimilarity(queryEmbedding, textEmbedding);
  }

  async getEmbedding(text) {
    const output = await this.pipeline(text, {
      pooling: 'mean',
      normalize: true,
    });

    return Array.from(output.data);
  }

  cosineSimilarity(vecA, vecB) {
    let dotProduct = 0;
    let normA = 0;
    let normB = 0;

    for (let index = 0; index < vecA.length; index += 1) {
      dotProduct += vecA[index] * vecB[index];
      normA += vecA[index] * vecA[index];
      normB += vecB[index] * vecB[index];
    }

    if (!normA || !normB) {
      return 0;
    }

    return dotProduct / (Math.sqrt(normA) * Math.sqrt(normB));
  }

  async search(dimensionCode, query, topK = 5) {
    await this.initialize();

    const candidates = this.dimensionValuesCache.filter((row) => row.dimension_code === dimensionCode);
    if (candidates.length === 0) {
      throw new Error(`维度 "${dimensionCode}" 没有配置的维度值`);
    }

    const scored = [];
    for (const row of candidates) {
      const searchText = this.buildSearchText(row);
      const lexical = this.lexicalScore(query, searchText);
      const semantic = await this.vectorScore(query, searchText);
      const similarity = this.useTransformers ? semantic * 0.7 + lexical * 0.3 : lexical;

      scored.push({
        value: row.value,
        value_desc: row.value_desc,
        similarity: Number(similarity.toFixed(4)),
      });
    }

    scored.sort((left, right) => right.similarity - left.similarity);
    return scored.slice(0, topK);
  }
}

module.exports = {
  SemanticSearch,
};
