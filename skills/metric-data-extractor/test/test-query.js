const assert = require('assert');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const skillDir = path.join(__dirname, '..');

function waitForServer(url) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      http
        .get(url, () => {
          clearInterval(timer);
          resolve();
        })
        .on('error', () => {
          if (attempts > 30) {
            clearInterval(timer);
            reject(new Error('mock server did not start'));
          }
        });
    }, 200);
  });
}

async function main() {
  const server = spawn('node', ['mock/mock-server.js'], {
    cwd: skillDir,
    stdio: 'ignore',
  });

  try {
    await waitForServer('http://127.0.0.1:3000/ads-data/openapi/v1/chart/common');
  } catch (_error) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  const child = spawn(
    'node',
    [
      'bin/query-metrics.js',
      '--metrics',
      '消耗,线索量',
      '--start-date',
      '2026-01-01',
      '--end-date',
      '2026-01-03',
      '--dimensions',
      '日期,渠道',
      '--filters',
      '{"推广对象":"元保"}',
      '--mock',
    ],
    {
      cwd: skillDir,
      env: { ...process.env, HUAWEI_ADS_APP_ID: 'demo', HUAWEI_ADS_SECRET: 'demo-secret' },
    },
  );

  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (chunk) => {
    stdout += chunk;
  });
  child.stderr.on('data', (chunk) => {
    stderr += chunk;
  });

  const exitCode = await new Promise((resolve) => child.on('close', resolve));
  server.kill('SIGTERM');

  assert.strictEqual(exitCode, 0, stderr);
  const parsed = JSON.parse(stdout);
  assert.ok(Array.isArray(parsed.data));
  assert.ok(parsed.total >= 1);
  assert.ok(Object.prototype.hasOwnProperty.call(parsed.data[0], 'cost'));

  console.log('✓ 查询工具测试通过');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
