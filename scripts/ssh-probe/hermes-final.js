const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "===== 1. Hermes 当前 FEISHU_ALLOW_BOTS 实际值 =====";
grep -E "^FEISHU_ALLOW_BOTS=" /root/.hermes/.env | sed -E 's/=(.*)/= [\\1]/';
grep -E "^FEISHU_REQUIRE_MENTION=" /root/.hermes/.env | sed -E 's/=(.*)/= [\\1]/';
echo;
echo "===== 2. Hermes 日志全部 sender 类型分布（8/10-8/12） =====";
grep -E "Inbound (group|dm) message received" /root/.hermes/logs/gateway.log 2>/dev/null | grep -oE "sender=[a-z]+:[a-zA-Z0-9_]+" | sort | uniq -c;
echo;
echo "===== 3. Hermes 日志里是否有任何 bot 来源消息（含 8/10） =====";
grep -E "sender=(app|bot|cli_)" /root/.hermes/logs/gateway.log 2>/dev/null | head -5;
grep -cE "bot" /root/.hermes/logs/gateway.log 2>/dev/null;
echo;
echo "===== 4. 8/10 是否有收到消息记录 =====";
grep "2026-08-10" /root/.hermes/logs/gateway.log 2>/dev/null | grep -E "Inbound" | head -5 | cut -c1-200;
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
