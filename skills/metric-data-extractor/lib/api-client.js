const crypto = require('crypto');
const axios = require('axios');

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

      // Only add Authorization header if not using mock server
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

module.exports = {
  HuaweiAdsClient,
};
