const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "===== 1. briefing 脚本位置 =====";
find /root/.hermes -maxdepth 3 -name "briefing*" -o -maxdepth 3 -name "market_snapshot*" -o -maxdepth 3 -name "server_morning_check*" 2>/dev/null | grep -v node_modules | head -10;
echo;
echo "===== 2. market-data-briefing skill =====";
find /root/.hermes/skills -maxdepth 2 -iname "*market*" 2>/dev/null | head -5;
echo;
echo "===== 3. 今天早盘 cron 运行状态 =====";
python3 -c "
import json
d = json.load(open('/root/.hermes/cron/jobs.json'))
for j in d.get('jobs', []):
    if '简报' in j.get('name','') or 'briefing' in j.get('skill',''):
        print('name:', j['name'], '| last:', j.get('last_run_at'), '| status:', j.get('last_status'), '| next:', j.get('next_run_at'))
";
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
