const fs = require("fs");
const { Client } = require("ssh2");
const conn = new Client();
conn.on("ready", () => {
  conn.exec("ls -la /root/codehub 2>/dev/null || ls -la ~/codehub 2>/dev/null || find / -maxdepth 3 -type d -name codehub 2>/dev/null | head -3", (err, stream) => {
    if (err) { console.log("EXEC_ERR", err.message); conn.end(); return; }
    let out = "";
    stream.on("close", (code) => { console.log(out || "(搬瓦工无 codehub 目录)"); conn.end(); });
    stream.on("data", (d) => { out += d.toString(); });
  });
}).on("error", (e) => { console.log("CONN_ERR:", e.message); process.exit(1); });
conn.connect({ host: "173.242.113.39", port: 22, username: "root", privateKey: fs.readFileSync("/home/node/.openclaw/workspace/.ssh/bwh_diag"), readyTimeout: 10000 });
