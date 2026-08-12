const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "=== gateway.log 最后 15 行原文 ===";
tail -15 /root/.hermes/logs/gateway.log 2>/dev/null;
echo "=== errors.log 最后 5 行 ===";
tail -5 /root/.hermes/logs/errors.log 2>/dev/null;
echo "=== 最近是否有飞书相关（13:00后）===";
tail -200 /root/.hermes/logs/gateway.log 2>/dev/null | grep -iE "feishu|lark|飞书" | tail -5;
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
