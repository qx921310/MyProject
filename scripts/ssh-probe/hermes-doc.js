const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "===== Hermes 文档：allow_bots 取值语义 =====";
grep -B2 -A6 "allow_bots" /root/.hermes/cache/web/hermes-agent.nousresearch.com-1e46f93c4e.md 2>/dev/null | head -40;
echo;
echo "===== Hermes 源码：allow_bots 处理 =====";
grep -rn "allow_bots" /usr/local/lib/hermes-agent/venv/lib/python*/site-packages/hermes_plugins/feishu_platform/*.py 2>/dev/null | head -10;
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
