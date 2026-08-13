#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const {
  fetchTradingViewXau,
  DEFAULT_SYMBOL,
} = require('./fetch_tv_xau');

const TX_URL = 'https://qt.gtimg.cn/q=hf_XAU';
const TX_TIMEOUT_MS = 10000;
const MAX_AGE_SECONDS = 120;

/**
 * 从腾讯行情抓取伦敦金现货 hf_XAU。
 *
 * @param {number} timeoutMs 超时毫秒数
 * @returns {Promise<{price:number, ts_tx:number, quote_time:string, raw:string}>}
 */
async function fetchTencentXau(timeoutMs = TX_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(TX_URL, {
      headers: {
        'user-agent': 'Mozilla/5.0',
      },
      signal: controller.signal,
    });

    if (!res.ok) {
      throw new Error(`Tencent HTTP ${res.status} ${res.statusText}`);
    }

    const buffer = new Uint8Array(await res.arrayBuffer());
    const text = new TextDecoder('gb18030').decode(buffer);
    return parseTencentXau(text);
  } catch (err) {
    if (err && err.name === 'AbortError') {
      throw new Error(`Tencent fetch timeout after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 解析腾讯 hf_XAU 返回串。
 * 示例：v_hf_XAU="4374.89,-0.76,...,16:38:00,...,2026-08-13,伦敦金（现货黄金）";
 */
function parseTencentXau(text) {
  const match = /v_hf_XAU="([^"]*)"/.exec(text || '');
  if (!match) {
    throw new Error('Tencent response does not contain v_hf_XAU');
  }

  const fields = match[1].split(',');
  const price = Number.parseFloat(fields[0]);
  if (!Number.isFinite(price)) {
    throw new Error(`Tencent XAU price is invalid: ${fields[0]}`);
  }

  const quoteTime = fields[6] || '';
  const quoteDate = fields[12] || '';
  const ts = parseTencentTimestamp(quoteTime, quoteDate);

  return {
    price,
    ts_tx: ts,
    quote_time: quoteTime,
    quote_date: quoteDate,
    raw: match[1],
  };
}

function parseTencentTimestamp(time, date) {
  if (!/^\d{1,2}:\d{2}:\d{2}$/.test(time || '') || !/^\d{4}-\d{2}-\d{2}$/.test(date || '')) {
    return null;
  }

  const parsed = Date.parse(`${date}T${time}+08:00`);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatTimestamp(ms) {
  if (ms === null || ms === undefined || ms === '') return 'null';
  if (!Number.isFinite(Number(ms))) return 'null';
  return new Date(Number(ms)).toISOString();
}

function computeDiffPct(sourceTx, sourceTv) {
  if (
    sourceTx === null || sourceTx === undefined || sourceTx === ''
    || sourceTv === null || sourceTv === undefined || sourceTv === ''
  ) {
    return null;
  }

  if (!Number.isFinite(Number(sourceTx)) || !Number.isFinite(Number(sourceTv))) {
    return null;
  }

  const tx = Number(sourceTx);
  const tv = Number(sourceTv);
  if (tx === 0) return null;

  const diff = Math.abs((tx - tv) / tx) * 100;
  return Math.round(diff * 1e6) / 1e6;
}

function computeAgeSeconds(timestampMs, nowMs) {
  if (
    timestampMs === null || timestampMs === undefined || timestampMs === ''
    || !Number.isFinite(Number(timestampMs))
    || !Number.isFinite(Number(nowMs))
  ) {
    return null;
  }

  const seconds = (Number(nowMs) - Number(timestampMs)) / 1000;
  return Math.round(seconds * 1000) / 1000;
}

function computeTimestampDelta(tsTx, tsTv) {
  if (
    tsTx === null || tsTx === undefined || tsTx === ''
    || tsTv === null || tsTv === undefined || tsTv === ''
    || !Number.isFinite(Number(tsTx))
    || !Number.isFinite(Number(tsTv))
  ) {
    return null;
  }

  const seconds = (Number(tsTx) - Number(tsTv)) / 1000;
  return Math.round(seconds * 1000) / 1000;
}

function makeVerdict(txStatus, tvStatus, diffPct, ageTx, ageTv) {
  if (txStatus === 'rejected' || tvStatus === 'rejected' || diffPct === null) {
    return 'warn';
  }
  if (diffPct > 0.5) return 'warn';
  if (
    ageTx === null || ageTx === undefined || ageTx > MAX_AGE_SECONDS
    || ageTv === null || ageTv === undefined || ageTv > MAX_AGE_SECONDS
  ) {
    return 'warn';
  }
  return 'ok';
}

async function run() {
  const args = process.argv.slice(2);
  let mode = 'json';
  if (args.includes('--text')) {
    mode = 'text';
  } else if (args.includes('--json')) {
    mode = 'json';
  }

  let symbol = DEFAULT_SYMBOL;
  const symbolIndex = args.indexOf('--symbol');
  if (symbolIndex !== -1 && args[symbolIndex + 1]) {
    symbol = args[symbolIndex + 1];
  }

  const [txResult, tvResult] = await Promise.allSettled([
    fetchTencentXau(),
    fetchTradingViewXau(symbol),
  ]);

  const tx = txResult.status === 'fulfilled' ? txResult.value.price : null;
  const tv = tvResult.status === 'fulfilled' ? tvResult.value.price : null;
  const tsTx = txResult.status === 'fulfilled' ? txResult.value.ts_tx : null;
  const tsTv = tvResult.status === 'fulfilled' ? tvResult.value.ts_tv : null;
  const diffPct = computeDiffPct(tx, tv);
  const now = Date.now();
  const ageTx = computeAgeSeconds(tsTx, now);
  const ageTv = computeAgeSeconds(tsTv, now);
  const tsDelta = computeTimestampDelta(tsTx, tsTv);
  const verdict = makeVerdict(txResult.status, tvResult.status, diffPct, ageTx, ageTv);

  const result = {
    source_tx: tx,
    source_tv: tv,
    diff_pct: diffPct,
    ts_tx: tsTx,
    ts_tv: tsTv,
    age_tx: ageTx,
    age_tv: ageTv,
    ts_delta: tsDelta,
    verdict,
  };

  let stdout = '';
  if (mode === 'json') {
    stdout = `${JSON.stringify(result, null, 2)}\n`;
  } else {
    const lines = [
      `腾讯 XAU       : ${tx === null ? 'null' : tx.toFixed(2)} @ ${formatTimestamp(tsTx)} (age ${ageTx === null ? 'null' : `${ageTx.toFixed(3)}s`})`,
      `TradingView XAU: ${tv === null ? 'null' : tv.toFixed(2)} @ ${formatTimestamp(tsTv)} (age ${ageTv === null ? 'null' : `${ageTv.toFixed(3)}s`}) (${symbol})`,
      `diff_pct       : ${diffPct === null ? 'null' : `${diffPct.toFixed(6)}%`}`,
      `ts_delta       : ${tsDelta === null ? 'null' : `${tsDelta.toFixed(3)}s`}`,
      `verdict        : ${verdict}`,
    ];

    if (verdict === 'warn') {
      lines.push('');
      lines.push('WARN: 双源数据不一致或至少一个数据源失败，请勿直接引用该价格。');
    }

    stdout = `${lines.join('\n')}\n`;
  }

  let stderr = '';
  if (txResult.status === 'rejected') {
    stderr += `Tencent fetch failed: ${txResult.reason && txResult.reason.message ? txResult.reason.message : txResult.reason}\n`;
  }
  if (tvResult.status === 'rejected') {
    stderr += `TradingView fetch failed: ${tvResult.reason && tvResult.reason.message ? tvResult.reason.message : tvResult.reason}\n`;
  }

  const exitCode = verdict === 'ok' ? 0 : 1;

  return {
    result,
    stdout,
    stderr,
    exitCode,
  };
}

async function main() {
  const { stdout, stderr, exitCode } = await run();

  try {
    if (stdout) fs.writeSync(1, stdout);
    if (stderr) fs.writeSync(2, stderr);
  } catch (_) {
    // 输出失败不影响退出码
  }
  process.exitCode = exitCode;
}

if (require.main === module) {
  main().catch((err) => {
    try {
      fs.writeSync(2, `${err && err.stack ? err.stack : String(err)}\n`);
    } catch (_) {
      // 忽略输出失败
    }
    process.exitCode = 1;
  });
}

module.exports = {
  fetchTencentXau,
  parseTencentXau,
  parseTencentTimestamp,
  formatTimestamp,
  computeDiffPct,
  computeAgeSeconds,
  computeTimestampDelta,
  makeVerdict,
  run,
};
