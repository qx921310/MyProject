const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "===== 1. Hermes 8/10-8/11 是否收到过 bot 消息（查 agent.log + gateway.log） =====";
grep -hE "sender=(bot|app|cli_)" /root/.hermes/logs/agent.log /root/.hermes/logs/gateway.log 2>/dev/null | head -8;
echo "--- 含 cli_（应用ID格式）的消息行 ---";
grep -hE "cli_" /root/.hermes/logs/agent.log 2>/dev/null | grep -iE "message|inbound|sender" | head -8 | cut -c1-220;
echo;
echo "===== 2. 8/10-8/11 日志里有没有『钻仔/小钻钻』作为发言者的记录 =====";
grep -hE "钻仔|小钻钻" /root/.hermes/logs/agent.log 2>/dev/null | head -6 | cut -c1-200;
echo;
echo "===== 3. Hermes 进程启动时间（确认 .env 改动后是否重启过） =====";
ps -o pid,lstart,cmd -p 93886 2>/dev/null;
echo "--- .env 修改时间 ---";
stat -c '%y %n' /root/.hermes/.env 2>/dev/null;
echo "--- gateway 重启记录 ---";
tail -5 /root/.hermes/gateway-starts.log 2>/dev/null;
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
