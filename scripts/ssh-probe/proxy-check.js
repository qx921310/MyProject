const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();

const COMMANDS = `
echo "===== 1. 8080 python3 进程详情 =====";
ps -fp 22433 2>/dev/null;
echo "--- 完整命令行 ---";
tr '\\0' ' ' < /proc/22433/cmdline 2>/dev/null; echo;
echo "--- 进程工作目录 ---";
ls -la /proc/22433/cwd 2>/dev/null;
echo "--- 8080 本地响应测试 ---";
curl -s -o /dev/null -w "http://127.0.0.1:8080 -> %{http_code}\\n" --max-time 5 http://127.0.0.1:8080/ 2>/dev/null;
curl -s --max-time 5 http://127.0.0.1:8080/ 2>/dev/null | head -20;
echo;
echo "===== 2. xray 进程详情 =====";
ps -fp 25832 2>/dev/null;
tr '\\0' ' ' < /proc/25832/cmdline 2>/dev/null; echo;
echo "--- 系统服务 ---";
systemctl list-units --type=service 2>/dev/null | grep -iE "xray|python|sub|proxy|v2ray" | head -10;
echo "--- 配置文件位置 ---";
ls -la /usr/local/etc/xray/ 2>/dev/null || ls -la /etc/xray/ 2>/dev/null || echo "(常规路径无配置)";
find / -maxdepth 4 -name "config.json" -path "*xray*" 2>/dev/null | head -3;
echo;
echo "===== 3. xray 运行状态 =====";
systemctl status xray 2>/dev/null | head -12 || service xray status 2>/dev/null | head -5;
echo "--- 443 端口连通（本地） ---";
curl -s -o /dev/null -w "https://127.0.0.1:443 -> %{http_code}\\n" --max-time 5 -k https://127.0.0.1:443/ 2>/dev/null || echo "443 无 HTTP 响应(可能仅代理协议)";
echo;
echo "===== 4. xray 日志尾部 =====";
journalctl -u xray -n 20 --no-pager 2>/dev/null | tail -20 || tail -30 /var/log/xray/error.log 2>/dev/null || echo "(无日志)";
echo;
echo "===== 5. 出网测试（本机直连 vs 走代理） =====";
echo "--- 直连 google ---";
curl -s -o /dev/null -w "%{http_code} (%{time_total}s)\\n" --max-time 8 https://www.google.com/ 2>/dev/null || echo "直连失败";
echo "--- 经 443 本地代理口测试(如为 socks/http) ---";
curl -s -o /dev/null -w "%{http_code}\\n" --max-time 8 -x socks5h://127.0.0.1:443 https://www.google.com/ 2>/dev/null || echo "非socks5";
curl -s -o /dev/null -w "%{http_code}\\n" --max-time 8 -x http://127.0.0.1:443 https://www.google.com/ 2>/dev/null || echo "非http代理";
echo;
echo "===== 6. 资源占用 =====";
ps -o pid,%cpu,%mem,rss,etime,comm -p 22433,25832 2>/dev/null;
echo;
echo "===== 7. 开放端口复核 =====";
ss -tlnp 2>/dev/null | grep -E "8080|443|22" | head -6;
`;

conn.on("ready", () => {
  console.log("[connected]");
  conn.exec(COMMANDS, (err, stream) => {
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
