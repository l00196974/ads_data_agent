#!/usr/bin/env node
const puppeteer = require('puppeteer-core');
const axios = require('axios');
const fs = require('fs');
const path = require('path');

const BASE_URL = 'https://wo-drcn.dbankcloud.cn';
const TARGET_URL = `${BASE_URL}/ads-data/#/digitalCockpit/volumeFluctuationAnalysis`;
const ADS_API_BASE = `${BASE_URL}/ads-data/api`;
const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
// headless 模式下使用持久化用户数据目录保存 SSO session cookie，
// 避免每次刷新都重新触发 MFA 验证码。
const PUPPETEER_USER_DATA_DIR = path.join(__dirname, '..', '.puppeteer-user-data');
const CACHE_FILE = path.join(__dirname, '..', '.wisedata-token-cache.json');

class WisedataClient {
  constructor({ username, password }) {
    this.username = username;
    this.password = password;
    this.csrfToken = null;
    this.cookie = null;
    this.tokenExpiresAt = null;
    this.browser = null;
    this.page = null;
  }

  async ensureValidToken() {
    if (this.isTokenValid()) {
      console.error('使用内存缓存的 token');
      return;
    }

    const cached = this.loadFromCache();
    if (cached && Date.now() < cached.expiresAt) {
      console.error('使用文件缓存 token');
      this.csrfToken = cached.csrfToken;
      this.cookie = cached.cookie;
      this.tokenExpiresAt = cached.expiresAt;
      return;
    }

    console.error('Token 无效或已过期，开始浏览器登录...');
    await this.browserLogin({ autoClose: true });
  }

  isTokenValid() {
    if (!this.csrfToken || !this.tokenExpiresAt) return false;
    return Date.now() < this.tokenExpiresAt - 5 * 60 * 1000;
  }

  loadFromCache() {
    try {
      if (fs.existsSync(CACHE_FILE)) {
        const data = JSON.parse(fs.readFileSync(CACHE_FILE, 'utf-8'));
        if (Date.now() < data.expiresAt) {
          return data;
        }
      }
    } catch (e) {}
    return null;
  }

  saveToCache() {
    try {
      const data = {
        csrfToken: this.csrfToken,
        cookie: this.cookie,
        expiresAt: this.tokenExpiresAt,
      };
      fs.writeFileSync(CACHE_FILE, JSON.stringify(data, null, 2));
      console.error('Token 已缓存到文件');
    } catch (e) {}
  }

  async browserLogin({ autoClose, headless } = {}) {
    const now = () => new Date().toLocaleString();
    const safeUrl = () => { try { return this.page?.url() || 'unknown'; } catch (e) { return 'unknown'; } };
    const ADS_DATA_PREFIX = 'https://wo-drcn.dbankcloud.cn/ads-data/';
    const PORTAL_PREFIX = 'https://wo-drcn.dbankcloud.cn/';
    const TOKEN_API = '/ads-data/api/user/currentUser';

    const isHeadless = headless === true;
    console.error(`[${now()}] [browserLogin] 启动 Chrome (headless=${isHeadless})...`);

    // 持久化 user data 目录保存 SSO session cookie，避免每次 headless 刷新都触发 MFA。
    // 交互式登录也使用同一目录，这样首次登录建立的 SSO session 后续 headless 刷新可直接复用。
    if (!fs.existsSync(PUPPETEER_USER_DATA_DIR)) {
      fs.mkdirSync(PUPPETEER_USER_DATA_DIR, { recursive: true });
    }

    this.browser = await puppeteer.launch({
      executablePath: CHROME_PATH,
      headless: isHeadless ? 'new' : false,
      userDataDir: PUPPETEER_USER_DATA_DIR,
      args: ['--disable-extensions', '--no-first-run', '--no-default-browser-check'],
    });

    try {
      const pages = await this.browser.pages();
      this.page = pages.length > 0 ? pages[0] : await this.browser.newPage();
      await this.page.setViewport({ width: 1400, height: 900 });

      // ── 导航到目标页面 ──
      console.error(`[${now()}] [browserLogin] 导航到目标页: ${TARGET_URL}`);
      await this.page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 30000 })
        .catch(e => console.error(`[${now()}] [browserLogin] 首次导航异常: ${e.message}`));

      // ── 等待用户登录 + 进入 ads-data ──
      // 流程：goto ads-data → 跳 SSO 登录 → 用户输账号密码+MFA → 门户概览页
      //        → 脚本自动跳转到 ads-data → SPA 加载 → 主动调 currentUser 取 token
      const deadline = Date.now() + 180000; // 最长 3 分钟（给用户留输验证码时间）
      let tokenFromHeader = null;
      let savedCookie = null;

      let portalSince = null;          // 进入门户页的时间戳（用于停留一定时间后再跳转）

      while (Date.now() < deadline) {
        const url = safeUrl();

        // ──────────────────────────────────────────────
        // 情形 1：URL 以 ads-data 开头 → 调 currentUser 取 token → 再取完整 cookie
        // ──────────────────────────────────────────────
        if (url.startsWith(ADS_DATA_PREFIX)) {
          console.error(`[${now()}] [browserLogin] 在 ads-data 页面，调 currentUser...`);

          // 先调 currentUser 取 x-csrf-token（让服务端响应把关键 cookie 也设进来）
          try {
            const result = await this.page.evaluate(async () => {
              const resp = await fetch('/ads-data/api/user/currentUser', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: '{}',
              });
              return { token: resp.headers.get('x-csrf-token'), status: resp.status };
            });
            if (result.token) {
              tokenFromHeader = result.token;
              // 取到 token 后再稍等一下，让可能的异步 cookie 都落盘
              await this.waitWithTimeout(2000);
              // 然后取完整 cookie
              const cookies = await this.page.cookies();
              savedCookie = cookies.map(c => `${c.name}=${c.value}`).join('; ');
              console.error(`[${now()}] [browserLogin] Cookie: ${cookies.length} 个 — ${cookies.map(c => c.name).join(', ')}`);
              console.error(`[${now()}] [browserLogin] 主动调 currentUser 取到 csrfToken`);
              break;
            }
            console.error(`[${now()}] [browserLogin] currentUser 响应 ${result.status}，无 x-csrf-token header`);
          } catch (e) {
            console.error(`[${now()}] [browserLogin] 调 currentUser 失败: ${e.message}`);
          }

          // 没拿到 token 时也取一次 cookie（兜底）
          if (!savedCookie) {
            try {
              const cookies = await this.page.cookies();
              savedCookie = cookies.map(c => `${c.name}=${c.value}`).join('; ');
              console.error(`[${now()}] [browserLogin] Cookie（兜底）: ${cookies.length} 个`);
            } catch (_) {}
          }

          await this.waitWithTimeout(3000);
          continue;
        }

        // ──────────────────────────────────────────────
        // 情形 2：在 wo-drcn.dbankcloud.cn 门户页（非 ads-data）
        //       → 停留 3 秒后再跳转到 ads-data
        //       排除含 /verification 等认证相关路径（仍在验证过程中）
        // ──────────────────────────────────────────────
        if (url.startsWith(PORTAL_PREFIX) && !url.includes('/verification')) {
          if (portalSince === null) {
            portalSince = Date.now();
            console.error(`[${now()}] [browserLogin] 进入门户页（${url}），记录停留时间...`);
          }

          const stayedMs = Date.now() - portalSince;
          if (stayedMs >= 3000) {
            console.error(`[${now()}] [browserLogin] 门户页停留 ${stayedMs / 1000}s，导航到 ads-data...`);
            portalSince = null;
            try {
              await this.page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 20000 });
            } catch (e) {
              console.error(`[${now()}] [browserLogin] 导航失败: ${e.message}`);
            }
            await this.waitWithTimeout(5000);
          } else {
            console.error(`[${now()}] [browserLogin] 门户页停留 ${stayedMs / 1000}s / 3s，等待中...`);
            await this.waitWithTimeout(3000);
          }
          continue;
        }

        // ──────────────────────────────────────────────
        // 情形 3：既不是 ads-data 也不是门户页（SSO 登录页、MFA 验证码页等）
        //       → 不干预，等用户完成
        // ──────────────────────────────────────────────
        portalSince = null;
        console.error(`[${now()}] [browserLogin] 不在 ads-data/门户页（${url}），等待用户完成登录...`);
        await this.waitWithTimeout(3000);
      }

      if (!tokenFromHeader) {
        const url = safeUrl();
        console.error(`[${now()}] [browserLogin] 等待超时，最终 URL: ${url}，未获取到 csrfToken`);
        throw new Error(`未获取到 csrfToken（最终 URL: ${url}）`);
      }

      // ── 提取完成 ──
      this.csrfToken = tokenFromHeader;
      this.cookie = savedCookie || '';

      if (!this.csrfToken || !this.cookie) {
        throw new Error('未提取到有效 token');
      }

      this.tokenExpiresAt = Date.now() + 60 * 60 * 1000;
      this.saveToCache();
      console.error(`[${now()}] [browserLogin] Token 缓存成功，有效期至: ${new Date(this.tokenExpiresAt).toLocaleString()}`);

    } finally {
      if (autoClose && this.browser) {
        console.error(`[${now()}] [browserLogin] 关闭浏览器...`);
        try { await this.browser.close(); } catch (_) {}
      }
    }
  }

  async waitWithTimeout(ms) {
    return new Promise(r => setTimeout(r, ms));
  }

  async tryFillLogin() {
    if (!this.username) {
      console.error('未配置用户名，请在浏览器页面中手动输入凭据完成登录');
      return;
    }
    try {
      await this.waitWithTimeout(2000);

      const usernameSelectors = [
        'input[type="text"]',
        'input[name="username"]',
        'input[placeholder*="用户"]',
        '#username',
        'input[id*="user"]',
      ];

      for (const sel of usernameSelectors) {
        const el = await this.page.$(sel);
        if (el) {
          console.error('找到用户名输入框:', sel);
          await el.click({ clickCount: 3 });
          await this.page.keyboard.type(this.username, { delay: 50 });
          break;
        }
      }

      await this.waitWithTimeout(500);

      const passSelectors = [
        'input[type="password"]',
        'input[name="password"]',
        'input[placeholder*="密码"]',
        '#password',
      ];

      for (const sel of passSelectors) {
        const el = await this.page.$(sel);
        if (el) {
          console.error('找到密码输入框:', sel);
          await el.click({ clickCount: 3 });
          await this.page.keyboard.type(this.password, { delay: 50 });
          break;
        }
      }

      await this.waitWithTimeout(500);

      const submitSelectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("登录")',
        'button:has-text("登陆")',
        'button:has-text("登 录")',
      ];

      for (const sel of submitSelectors) {
        try {
          const el = await this.page.$(sel);
          if (el) {
            console.error('点击提交按钮...');
            await el.click();
            break;
          }
        } catch (e) {}
      }

    } catch (e) {
      console.error('自动填写失败:', e.message);
    }
  }

  /**
   * 等待登录完成 — 监控 URL 直到不再包含登录关键词
   * 这可以处理多种情况：
   *   - 无 MFA：直接跳转到门户/ads-data
   *   - 有 MFA：用户输入验证码后跳转
   *   - session 有效：直接进入
   */
  async waitForLoginComplete(timeout) {
    const start = Date.now();
    const loginKeywords = ['login', 'uniportal', 'verification', 'auth'];

    while (Date.now() - start < timeout) {
      // 先检查是否已在 ads-data
      if (this.page.url().includes('/ads-data/')) {
        console.error('已在 ads-data 页面');
        return true;
      }

      // 检查 URL 是否完全不含登录关键词
      const url = this.page.url();
      const onLoginPage = loginKeywords.some(k => url.includes(k));

      if (!onLoginPage && url !== 'about:blank') {
        console.error('检测到离开登录页:', url);
        return true;
      }

      await this.waitWithTimeout(2000);
    }
    console.error('等待登录完成超时');
    return false;
  }

  async waitForUrlContains(substring, timeout) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      const url = this.page.url();
      if (url.includes(substring)) {
        console.error('检测到目标 URL:', url);
        return true;
      }
      await this.waitWithTimeout(2000);
    }
    console.error(`等待 URL 包含 "${substring}" 超时`);
    return false;
  }

  async extractTokens() {
    // 1. 从 cookies 提取
    const cookies = await this.page.cookies();
    this.cookie = cookies.map(c => `${c.name}=${c.value}`).join('; ');
    console.error('Cookie 数量:', cookies.length);
    console.error('Cookie names:', cookies.map(c => c.name).join(', '));

    // 尝试从 cookies 查找 x-csrf-token 或其他 token cookie
    const tokenCookies = cookies.filter(c =>
      c.name.toLowerCase().includes('csrf') ||
      c.name.toLowerCase().includes('token') ||
      c.name === 'x-csrf-token'
    );
    if (tokenCookies.length > 0) {
      // 优先用名字为 "x-csrf-token" 的 cookie
      const exactMatch = tokenCookies.find(c => c.name === 'x-csrf-token');
      this.csrfToken = exactMatch ? exactMatch.value : tokenCookies[0].value;
      console.error('从 cookies 提取到 csrfToken');
    }

    // 2. 从 localStorage/sessionStorage 找
    if (!this.csrfToken) {
      this.csrfToken = await this.page.evaluate(() => {
        const keys = ['csrfToken', 'CSRF_TOKEN', 'token', 'authToken', 'x-csrf-token', 'csrf-token', 'env_token'];
        for (const k of keys) {
          const v = localStorage.getItem(k) || sessionStorage.getItem(k);
          if (v) return v;
        }
        return null;
      });
      if (this.csrfToken) {
        console.error('从 storage 提取到 csrfToken');
      }
    }

    // 3. 最后手段：发 API 请求从响应头拿 x-csrf-token
    if (!this.csrfToken) {
      console.error('尝试从 API 响应头获取 csrfToken...');
      try {
        const result = await this.page.evaluate(async () => {
          const controller = new AbortController();
          setTimeout(() => controller.abort(), 10000);
          const resp = await fetch('/ads-data/api/user/currentUser', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
            signal: controller.signal,
          });
          return {
            csrfToken: resp.headers.get('x-csrf-token'),
            status: resp.status
          };
        });
        if (result.csrfToken) {
          this.csrfToken = result.csrfToken;
          console.error('从 API 响应头获取到 csrfToken');
        } else {
          console.error('API 响应头未包含 x-csrf-token, status:', result.status);
        }
      } catch (e) {
        console.error('从 API 获取 csrfToken 失败:', e.message);
      }
    }

    console.error('csrfToken:', this.csrfToken ? '已提取' : '未找到');
    if (this.cookie) {
      console.error('Cookie 长度:', this.cookie.length);
      console.error('Cookie 前 100 字符:', this.cookie.substring(0, 100) + '...');
    }
  }

  async query(requestBody) {
    await this.ensureValidToken();

    try {
      const response = await axios.post(
        `${ADS_API_BASE}/decrease/indicator/data/table`,
        requestBody,
        {
          headers: {
            'Content-Type': 'application/json; charset=UTF-8',
            'Accept': 'application/json, text/plain, */*',
            'Cookie': this.cookie,
            'x-csrf-token': this.csrfToken,
            'origin': BASE_URL,
            'referer': `${BASE_URL}/ads-data/`,
            'x-portal-site': 'portal',
            'x-lang': 'zh',
          },
          timeout: 60000,
        }
      );

      if (response.data.code !== 200) {
        throw new Error(`API错误: ${response.data.message || 'unknown error'}`);
      }

      return response;
    } catch (error) {
      if (error.response?.status === 401 || error.response?.status === 403) {
        console.error('Token 已失效，尝试重新登录...');
        this.tokenExpiresAt = 0;
        await this.ensureValidToken();
        return this.query(requestBody);
      }
      if (error.response) {
        throw new Error(`API请求失败: ${error.response.status} - ${error.response.data?.message || error.message}`);
      }
      if (error.request) {
        throw new Error('API请求超时或网络错误');
      }
      throw error;
    }
  }
}

async function main() {
  const username = process.argv[2] || process.env.WISEDATA_USER;

  const client = new WisedataClient({ username, password: '' });

  try {
    console.error('打开浏览器，请在页面中手动完成登录...');
    await client.browserLogin({ autoClose: true });
    console.error('登录完成，Token 已缓存');

    if (username && client.csrfToken) {
      console.error('开始查询指标...');
      const requestBody = {
        dateTimeFilter: [{ start: '1747065600000', end: '1778601599000' }],
        indicators: [
          { indicatorKey: 'cost' },
          { indicatorKey: 'exposure' },
          { indicatorKey: 'cpm' },
        ],
        timingDimension: 'day',
      };

      const result = await client.query(requestBody);
      console.log(JSON.stringify(result.data, null, 2));
    }
  } catch (error) {
    console.error('错误:', error.message);
    process.exit(1);
  }
}

if (require.main === module) {
  main();
}

module.exports = { WisedataClient };
