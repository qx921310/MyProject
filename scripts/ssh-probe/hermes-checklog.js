const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "=== 日志文件 ===";
find /root/.hermes -maxdepth 2 -name "*.log" 2>/dev/null | head -5;
ls -la /root/.hermes/logs/ 2>/dev/null | tail -8;
echo "=== 最近 feishu 活动（13:00-13:10 北京 = 05:00-05:10 UTC）===";
LOG=$(find /root/.hermes -maxdepth 2 -name "*.log" 2>/dev/null | head -1);
tail -100 "$LOG" 2>/dev/null | grep -iE "feishu|message|deliver|webhook|websocket|error" | tail -15;
echo "=== 进程 CPU/状态 ===";
ps -o pid,etime,%cpu,%mem,stat,cmd -p 130013 2>/dev/null;
`;
conn.on("ready", () => {
  console.log("[connected]");
  conn.exec(CMD, (err, stream) => {
    if (err) { console.log("EXEC_ERR", err.message); conn.end(); return; }
    let out = "";
    stream.on("close", (code) => { console.log(out); console.log("EXIT:", code); conn.end(); });
    stream.on("data", (d) => { out += d.toString(); });
    stream.stderr.on("data", (d) => { console.log("ERR:", d.toString()); });
  });
}).on("error", (e) => { console.log("CONN_ERR:", e.message); process.exit(1); });
conn.connect({
  host: "173.242.113.39", port: 22, username: "root",
  privateKey: fs.readFileSync("/home/node/.openclaw/workspace/.ssh/bwh_diag"),
});
