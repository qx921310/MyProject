#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const TradingView = require('@mathieuc/tradingview');
const WebSocket = require('ws');

const DEFAULT_SYMBOL = 'XAUUSD';
const TIMEOUT_MS = 15000;

// 只跟踪本模块主动创建的 WebSocket，用于在 CONNECTING 状态强制断开，
// 弥补 @mathieuc/tradingview 的 client.end() 在 readyState === 0 时直接跳过的问题。
const trackedOwnerSockets = new WeakMap();
const socketToOwner = new WeakMap();
const originalWsOn = WebSocket.prototype.on;
let activeOwner = null;

function trackSocket(socket, owner) {
  let sockets = trackedOwnerSockets.get(owner);
  if (!sockets) {
    sockets = new Set();
    trackedOwnerSockets.set(owner, sockets);
  }
  sockets.add(socket);
  socketToOwner.set(socket, owner);
}

function untrackSocket(socket) {
  const owner = socketToOwner.get(socket);
  if (!owner) return;

  socketToOwner.delete(socket);
  const sockets = trackedOwnerSockets.get(owner);
  if (sockets) sockets.delete(socket);
}

WebSocket.prototype.on = function onWithTracking(event, listener) {
  if (event === 'open' && activeOwner) {
    trackSocket(this, activeOwner);
  }

  if (event === 'close') {
    const wrapped = function closeWithTracking(...args) {
      untrackSocket(this);
      return listener.apply(this, args);
    };
    return originalWsOn.call(this, event, wrapped);
  }

  return originalWsOn.call(this, event, listener);
};

async function closeTradingViewClient(client, owner) {
  await new Promise((resolve) => {
    const sockets = trackedOwnerSockets.get(owner)
      ? Array.from(trackedOwnerSockets.get(owner))
      : [];

    let done = false;
    const finishClose = () => {
      if (done) return;
      done = true;
      resolve();
    };

    client.onDisconnected(finishClose);
    for (const socket of sockets) {
      if (socket.readyState !== WebSocket.CLOSED) {
        socket.once('close', finishClose);
      }
    }

    if (
      sockets.length === 0
      || sockets.every((socket) => socket.readyState === WebSocket.CLOSED)
    ) {
      finishClose();
      return;
    }

    try {
      client.end();
    } catch (_) {
      // 即使库方法抛错，仍继续对底层 socket 做兜底清理。
    }

    for (const socket of sockets) {
      if (socket.readyState === WebSocket.CONNECTING) {
        try {
          socket.terminate();
        } catch (_) {
          // 强制终止失败时交给后续 close 事件或调用方超时。
        }
      } else if (socket.readyState === WebSocket.OPEN) {
        try {
          socket.close();
        } catch (_) {
          // 同上。
        }
      }
    }
  });
}

/**
 * 从 TradingView 拉取 XAUUSD 实时 1 分钟 K 线。
 * 匿名访问，无需 API key。
 *
 * @param {string} symbol TradingView symbol，默认 XAUUSD（实测解析为 OANDA:XAUUSD）
 * @param {number} timeoutMs 超时毫秒数
 * @returns {Promise<object>} 成功时返回含 open/high/low/close/time 的对象
 */
async function fetchTradingViewXau(symbol = DEFAULT_SYMBOL, timeoutMs = TIMEOUT_MS) {
  if (!symbol || typeof symbol !== 'string') {
    throw new TypeError('symbol must be a non-empty string');
  }

  const timeout = Number(timeoutMs);
  if (!Number.isFinite(timeout) || timeout <= 0) {
    throw new RangeError('timeoutMs must be a positive number');
  }

  return new Promise((resolve, reject) => {
    const owner = {};
    const previousOwner = activeOwner;
    activeOwner = owner;

    let client;
    try {
      client = new TradingView.Client();
    } finally {
      activeOwner = previousOwner;
    }

    const chart = new client.Session.Chart();

    let settled = false;
    let timer = null;
    const errors = [];

    const finish = (error, data) => {
      if (settled) return;
      settled = true;

      if (timer) clearTimeout(timer);

      (async () => {
        try {
          try {
            chart.delete();
          } catch (_) {
            // 清理阶段失败不应覆盖主结果。
          }
          await closeTradingViewClient(client, owner);
        } catch (cleanupError) {
          reject(error || cleanupError);
          return;
        }

        if (error) reject(error);
        else resolve(data);
      })();
    };

    const isValidPeriod = (p) => {
      if (!p) return false;
      return (
        Number.isFinite(Number(p.time)) &&
        Number.isFinite(Number(p.open)) &&
        Number.isFinite(Number(p.close)) &&
        Number.isFinite(Number(p.max)) &&
        Number.isFinite(Number(p.min))
      );
    };

    const tryResolve = () => {
      if (settled) return;
      const period = chart.periods[0];
      if (!isValidPeriod(period)) return;

      finish(null, {
        ok: true,
        symbol,
        resolved_symbol: chart.infos && chart.infos.full_name ? chart.infos.full_name : null,
        description: chart.infos && chart.infos.description ? chart.infos.description : null,
        currency: chart.infos && chart.infos.currency_code ? chart.infos.currency_code : 'USD',
        price: Number(period.close),
        open: Number(period.open),
        high: Number(period.max),
        low: Number(period.min),
        close: Number(period.close),
        time: Number(period.time),
        ts_tv: Number(period.time) * 1000,
        time_iso: new Date(Number(period.time) * 1000).toISOString(),
        source: 'TradingView',
      });
    };

    chart.onError((...args) => {
      errors.push(args.map(String).join(' '));
      if (!settled) {
        finish(new Error(`TradingView chart error: ${errors[errors.length - 1]}`));
      }
    });

    client.onError((...args) => {
      const message = args.map(String).join(' ');
      errors.push(message);
      if (!settled) {
        finish(new Error(`TradingView client error: ${message}`));
      }
    });

    chart.onSymbolLoaded(() => {
      // 有些市场可能先给 symbol 元数据，稍后再推送价格。
      setTimeout(tryResolve, 100);
    });

    chart.onUpdate(() => {
      tryResolve();
    });

    chart.setMarket(symbol, {
      timeframe: '1',
      range: 3,
    });

    timer = setTimeout(() => {
      finish(new Error(`TradingView fetch timeout after ${timeout}ms for ${symbol}`));
    }, timeout);
  });
}

function writeSync(fd, text) {
  try {
    fs.writeSync(fd, text);
  } catch (_) {
    // 输出失败不影响结果。
  }
}

async function main() {
  const symbol = process.argv[2] || DEFAULT_SYMBOL;
  try {
    const result = await fetchTradingViewXau(symbol, TIMEOUT_MS);
    writeSync(1, `${JSON.stringify(result, null, 2)}\n`);
    process.exitCode = 0;
  } catch (err) {
    writeSync(2, `${JSON.stringify({
      ok: false,
      symbol,
      error: err && err.message ? err.message : String(err),
    }, null, 2)}\n`);
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main();
}

module.exports = {
  fetchTradingViewXau,
  DEFAULT_SYMBOL,
  TIMEOUT_MS,
};
