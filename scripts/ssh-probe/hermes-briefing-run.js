const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "===== briefing_collect.sh 内容（前60行） =====";
head -60 /root/.hermes/scripts/briefing_collect.sh 2>/dev/null;
echo;
echo "===== 脚本目录 =====";
ls -la /root/.hermes/scripts/ | grep -E "briefing|market|morning" | head;
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
