const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();

const COMMANDS = `
echo "===== 1. 系统概况 =====";
hostname; uname -a; uptime; cat /etc/os-release 2>/dev/null | head -2;
echo;
echo "===== 2. CPU =====";
nproc; grep "model name" /proc/cpuinfo | head -1;
echo;
echo "===== 3. 内存 =====";
free -h;
echo;
echo "===== 4. 磁盘 =====";
df -hT / /home /root 2>/dev/null | grep -v tmpfs;
echo;
echo "===== 5. 网络 =====";
ip -4 addr show 2>/dev/null | grep -E "^[0-9]+:|inet " | head -10;
echo "--- 公网出口 ---";
curl -s --max-time 6 ifconfig.me 2>/dev/null; echo;
echo;
echo "===== 6. 监听端口 =====";
ss -tlnp 2>/dev/null | head -15 || netstat -tlnp 2>/dev/null | head -15;
echo;
echo "===== 7. Hermes 相关进程 =====";
ps aux 2>/dev/null | grep -iE "hermes|node|gateway" | grep -v grep | head -8;
echo;
echo "===== 8. Hermes 日志尾部 =====";
tail -30 /root/.hermes/logs/gateway.log 2>/dev/null || echo "(无 /root/.hermes/logs/gateway.log)";
echo;
echo "===== 9. 系统服务状态 =====";
systemctl list-units --type=service --state=running 2>/dev/null | grep -iE "hermes|gateway|openclaw|node" | head -5 || echo "(systemctl 不可用)";
echo;
echo "===== 10. 安全/资源告警 =====";
last -5 2>/dev/null | head -5; echo "--- 登录失败统计 ---";
grep "Failed password" /var/log/auth.log 2>/dev/null | wc -l;
echo "--- 最近5条失败 ---";
grep "Failed password" /var/log/auth.log 2>/dev/null | tail -5;
`;

conn.on("ready", () => {
  console.log("[connected]");
  conn.exec(COMMANDS, (err, stream) => {
    if (err) { console.log("EXEC_ERR", err.message); conn.end(); return; }
    let out = "";
    stream.on("close", (code) => {
      console.log(out);
      console.log("EXIT:", code);
      conn.end();
    });
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
