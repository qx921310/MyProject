const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "===== 1. Hermes 目录 =====";
ls -la /root/.hermes/ 2>/dev/null | head -40;
echo;
echo "===== 2. 配置文件候选 =====";
find /root/.hermes -maxdepth 2 -type f \\( -name "*.json" -o -name "*.yaml" -o -name "*.yml" -o -name "*.toml" -o -name "*.env" \\) 2>/dev/null | grep -v node_modules | head -30;
echo;
echo "===== 3. allow_bots 命中 =====";
grep -rn "allow_bots\\|allowBots\\|allow-bots" /root/.hermes/ 2>/dev/null | grep -v node_modules | head -30;
echo;
echo "===== 4. Hermes 进程 =====";
ps aux | grep -iE "hermes" | grep -v grep | head -8;
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
  host: "173.242.113.39",
  port: 22,
  username: "root",
  privateKey: fs.readFileSync("/home/node/.openclaw/workspace/.ssh/bwh_diag"),
  readyTimeout: 10000,
});
