const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "===== 1. Hermes config.yaml: feishu/bot/mention 相关行 =====";
grep -nE "allow_bots|allowBots|require_mention|requireMention|mention|bot" /root/.hermes/config.yaml 2>/dev/null | head -30;
echo;
echo "===== 2. Hermes .env: 相关键名（只看键名不看值） =====";
grep -oE "^[A-Za-z_]*?(ALLOW|BOT|MENTION|FEISHU)[A-Za-z_]*?=" /root/.hermes/.env 2>/dev/null | sort -u | head -20;
echo;
echo "===== 3. 是否有显式 allow_bots 值 =====";
grep -nE "allow_bots|allowBots" /root/.hermes/config.yaml /root/.hermes/.env 2>/dev/null | sed 's/=.*/=<redacted>/' | head -10;
echo;
echo "===== 4. Hermes 侧近期收到的消息（含我发的） =====";
grep -E "Inbound group message received|Inbound.*message received" /root/.hermes/logs/gateway.log 2>/dev/null | tail -8 | cut -c1-200;
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
conn.connect({
  host: "173.242.113.39", port: 22, username: "root",
  privateKey: fs.readFileSync("/home/node/.openclaw/workspace/.ssh/bwh_diag"),
  readyTimeout: 10000,
});
