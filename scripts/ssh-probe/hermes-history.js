const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "===== 1. 金仔日志：8/11 及更早是否收到过钻仔(bot)消息 =====";
grep -E "Inbound.*message received" /root/.hermes/logs/gateway.log 2>/dev/null | grep -E "sender=(app|bot)" | head -10;
echo "--- 搜『钻仔/小钻钻』提及 ---";
grep -E "钻仔|小钻钻" /root/.hermes/logs/gateway.log 2>/dev/null | tail -8 | cut -c1-220;
echo;
echo "===== 2. Hermes allow_bots 默认值（源码/文档） =====";
grep -rn "allow_bots" /usr/local/lib/hermes-agent/venv/lib/python*/site-packages/hermes_plugins/feishu_platform/*.py 2>/dev/null | head -8;
grep -rn "allow_bots" /root/.hermes/cache/web/hermes-agent.nousresearch.com-1e46f93c4e.md 2>/dev/null | head -6;
`;
conn.on("ready", () => {
  conn.exec(CMD, (err, stream) => {
    if (err) { console.log("EXEC_ERR", err.message); conn.end(); return; }
    let out = "";
    stream.on("close", () => { console.log(out); conn.end(); });
    stream.on("data", (d) => { out += d.toString(); });
    stream.stderr.on("data", (d) => { console.log("ERR:", d.toString()); });
  });
}).on("error", (e) => { console.log("CONN_ERR:", e.message); process.exit(1); });
conn.connect({ host: "173.242.113.39", port: 22, username: "root",
  privateKey: fs.readFileSync("/home/node/.openclaw/workspace/.ssh/bwh_diag"), readyTimeout: 10000 });
