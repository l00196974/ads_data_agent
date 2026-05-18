#!/usr/bin/env node
/**
 * Wisedata Token 刷新脚本
 *
 * 在 token 过期前主动刷新缓存，利用浏览器 session cookie 可能
 * 仍然有效的特点，避免触发 MFA（短信验证码）重新登录。
 *
 * 使用方式：
 *   node lib/refresh-wisedata-token.js
 *
 * 不传用户名，用户自己在浏览器页面中手动输入凭据完成登录。
 * 刷新成功后自动关闭浏览器并退出。
 */
const { WisedataClient } = require('./wisedata-client');

async function main() {
  console.error(`[${new Date().toLocaleString()}] 开始刷新 wisedata token...`);

  const client = new WisedataClient({ username: '', password: '' });

  try {
    // headless: true — 后台静默刷新，不弹浏览器窗口（适用于定时任务自动刷新）
    // 如果 SSO session 已过期导致静默刷新失败，需手动跑 node lib/wisedata-client.js 交互式登录
    await client.browserLogin({ autoClose: true, headless: true });

    if (client.csrfToken && client.cookie) {
      console.error(`[${new Date().toLocaleString()}] Token 刷新成功，有效期至: ${new Date(client.tokenExpiresAt).toLocaleString()}`);
    } else {
      console.error(`[${new Date().toLocaleString()}] Token 刷新失败，未获取到有效凭证`);
      process.exit(1);
    }
  } catch (error) {
    console.error(`[${new Date().toLocaleString()}] 刷新失败:`, error.message);
    process.exit(1);
  }
}

main();
