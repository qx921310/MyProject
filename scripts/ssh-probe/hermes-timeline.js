const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "===== 1. Hermes .env 备份对比：allow_bots / 相关键（只看键名与布尔值） =====";
for f in /root/.hermes/.env /root/.hermes/.env.bak-20260811-164512 /root/.hermes/.env.bak.20260810_143643 /root/.hermes/.env.bak.20260810_144047; do
  echo "--- \$(basename \$f) ---";
  grep -E "ALLOW_BOTS|ALLOWBOTS|REQUIRE_MENTION|ALLOW_BOT" \$f 2>/dev/null | sed -E 's/(=.*)/=<v>/';
done;
echo;
echo "===== 2. Hermes 日志：8/11 是否有 bot/app 来源消息 =====";
grep -E "sender=(app|bot|cli_)" /root/.hermes/logs/gateway.log 2>/dev/null | head -10;
echo "--- 8/11 全天群消息 sender 类型分布（前30条） ---";
grep -E "Inbound group message received" /root/.hermes/logs/gateway.log 2>/dev/null | grep -oE "sender=[a-z]+:[a-zA-Z0-9_]+" | sort | uniq -c | head;
echo;
echo "===== 3. Hermes 日志最早记录（网关上线时间） =====";
head -5 /root/.hermes/logs/gateway.log 2>/dev/null | cut -c1-180;
echo "--- 8/10-8/11 记录数 ---";
grep -c "2026-08-10" /root/.hermes/logs/gateway.log 2>/dev/null;
grep -c "2026-08-11" /root/.hermes/logs/gateway.log 2>/dev/null;
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
