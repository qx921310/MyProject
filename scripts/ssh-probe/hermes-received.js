const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
const CMD = `
echo "===== 1. Hermes 是否收到过我发的消息（搜我发的消息ID） =====";
grep -E "om_x100b68887067b4a0dd47573f8c0eec5|om_x100b6888242390acc45f0e8cde90f07" /root/.hermes/logs/gateway.log 2>/dev/null | tail -5 | cut -c1-250;
echo "(上面两条是我 00:52 和 01:04 发的测试消息ID)";
echo;
echo "===== 2. Hermes config.yaml 完整 feishu 段 =====";
grep -nA 30 "^feishu:" /root/.hermes/config.yaml 2>/dev/null | grep -vE "app_secret|secret|token|key|password" | head -40;
echo;
echo "===== 3. Hermes .env FEISHU 键的布尔值（只看真假类） =====";
grep -E "^FEISHU_(ALLOW|REQUIRE|GROUP)" /root/.hermes/.env 2>/dev/null | sed -E 's/=(.*)/=<redacted>/' | head;
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
