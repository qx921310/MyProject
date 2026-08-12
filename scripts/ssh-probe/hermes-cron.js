const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "===== 1. Hermes cron jobs =====";
python3 -c "
import json
d = json.load(open('/root/.hermes/cron/jobs.json'))
print(json.dumps(d, ensure_ascii=False, indent=1)[:1500])
" 2>/dev/null || cat /root/.hermes/cron/jobs.json 2>/dev/null | head -50;
echo;
echo "===== 2. 搜早盘相关 =====";
grep -rn "早盘\|盘前" /root/.hermes/ --include="*.json" --include="*.yaml" --include="*.md" --include="*.log" 2>/dev/null | grep -v node_modules | head -8 | cut -c1-200;
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
