# 《方案评审报告》——CodeHub 梯子基础设施项目化（B 方案：凭证加密入库）

- 评审日期：2026-08-12
- 评审人：Codex
- 评审依据：vpn-proxy-server skill（SKILL.md 全文 + 3 份 references + 客户端模板）+ 服务器现状实测（systemd 单元、三份服务配置、订阅产物、端口监听、cron、依赖工具、git 状态）
- 评审范围：B 方案骨架（docs / templates / .env.example / scripts / secrets），仅输出评审结论，不含实现代码

---

## 0. 结论摘要

方案总体成立：目录骨架方向正确，"模板 + 加密凭证 + 脚本"的分层能支撑起可维护性目标，旧服务暂不动、验收后再迁移的策略也稳妥。但有 4 点必须在进入阶段 2 前修订：

1. **凭证加密建议从 age 对称口令改为 age 非对称（X25519）**。对称方案的口令本身仍是必须保护、必须备份的 secret，传播面更大；非对称方案只有一个 identity 私钥文件，更简单可靠。age 二进制当前未安装，需纳入部署前置步骤。
2. **骨架缺 4 块**：客户端配置生成（hysteria2 客户端 / clash.yaml / vless URI）、订阅产物的单一来源生成与发布、证书轮换 SOP、备份/恢复演练。
3. **迁移的本质是"停旧启新"的瞬间切换**，因为 443 / 38475(UDP) / 8080 三个端口无法新旧并行占位。需要脚本化切换 + 自动回滚 + 验收门禁，切换窗口内订阅拉取会闪断。
4. **订阅生态是一个"状态面"**：clash.yaml、sub.txt、sub64.txt、subscription-links.txt、node-link.txt、sub-token 必须由同一份来源生成并同批更新，否则 key/uuid 轮换必漏同步——skill 中明确记录过此类事故。

---

## 1. 目录结构与分层（评审点 1）

### 1.1 总体评价

"docs / templates / .env.example / scripts / secrets"五块的分层合理：模板与配置分离、明文与加密分离、文档与代码分离，符合小项目应有的克制。缺口在于**只规划了"静态骨架"，没有覆盖运行时的状态面与操作面**（订阅产物、链接文档、证书、备份恢复、轮换）。

### 1.2 服务器现状盘点（评审事实依据）

| 服务 | 端口/协议 | 配置路径 | systemd 单元 | 运行身份 | 二进制 |
|---|---|---|---|---|---|
| hysteria-server | UDP 38475（QUIC） | /etc/hysteria/config.yaml | /etc/systemd/system/hysteria-server.service | User=hysteria + capability 裁剪 | /usr/local/bin/hysteria |
| xray | TCP 443（VLESS+Reality） | /usr/local/etc/xray/config.json | /etc/systemd/system/xray.service | **root（无 User=/NoNewPrivileges）** | /opt/xray/xray（另有 xray-26.3.27.bak） |
| sub-server | TCP 8080 | /var/www/sub/<SUB_TOKEN>/（clash.yaml、sub.txt、sub64.txt） | /etc/systemd/system/sub-server.service | nobody | /usr/bin/python3 -m http.server |

其他事实：

- 服务端公网 IP：173.242.113.39（LA）；hysteria 证书为自签（CN=bing.com，SAN 含 DNS:bing.com + IP，有效期至 2036-08）。
- 订阅发布面还有 /root/sub-token.txt、/root/subscription-links.txt、/root/node-link.txt 三份手工维护文件；目录名即订阅 token。
- root 与 hermes 均无 crontab，订阅文件目前是会话内手工生成，无自动更新。
- 仓库无 git remote（纯本地）；根 .gitignore 已有 .env、*.key、*.pem、token* 等规则，无 *.enc（符合入库预期）。

### 1.3 建议目录结构

```text
projects/proxy/
├── README.md                  # 项目说明、快速上手、变量来源
├── .env.example               # 非敏感变量 + 敏感变量名清单（不含值）
├── .gitignore                 # 兜底：明文/中间产物永不入库
├── docs/
│   ├── architecture.md        # 双协议拓扑、流量路径、端口清单、服务清单
│   ├── deploy.md              # 部署/迁移步骤，含 pre-flight 检查清单
│   ├── operations.md          # 启停/日志/排障流程
│   ├── rollback.md            # 回滚步骤 + 恢复演练说明
│   ├── rotation.md            # 凭证/证书轮换 SOP（含全部产物同步清单）
│   └── backup.md              # 备份范围、周期、恢复步骤
├── templates/
│   ├── systemd/               # hysteria-server / xray / sub-server 单元模板
│   ├── hysteria/config.yaml   # 含 {{VAR}} 占位符
│   ├── xray/config.json       # 含 {{VAR}} 占位符
│   ├── sub/                   # clash.yaml、sub.txt、links 模板
│   └── client/                # hysteria2-client.yaml（CA 固定版，本地验证用）
├── scripts/
│   ├── render.py              # 占位符渲染（stdlib）
│   ├── decrypt-secrets.sh     # age 解密 → 临时 .env（用后即删）
│   ├── deploy.sh              # 备份 → 渲染 → 校验 → 停旧 → 启新 → 验证 → 失败回滚
│   ├── rollback.sh            # 恢复备份 + 启旧停新
│   ├── backup.sh              # 全量备份（含 /root 下 token/links）
│   ├── generate_sub.py        # 单一来源生成全部订阅产物
│   └── test_health.sh         # 验收检查脚本
└── secrets/
    ├── README.md              # 字段清单：来源、用途、谁需要、如何解密
    └── *.enc                  # age 加密后的凭证（唯一入库内容）
```

### 1.4 缺口逐条（骨架之外必须补的）

a) **客户端配置生成**：骨架只有服务端模板。需要客户端侧模板——hysteria2-client.yaml（skill 明确推荐 CA 固定而非 insecure）、clash.yaml 节点段、vless:// / hysteria2:// URI——用于本地端到端验证和订阅产物生成。skill 的 references 里已有现成可用形态可参考。

b) **订阅更新机制**：现状是静态文件手工生成，属"一次性部署"，一旦轮换就漏同步。应把 clash.yaml / sub.txt / sub64.txt / links / node-link 全部交给一个生成脚本，从同一份渲染后的变量来源产出，再决定用 cron 还是 systemd timer 定期发布（1GB 小机建议一次性任务，非常驻进程）。

c) **证书轮换 SOP**：hysteria 使用自签证书（现有效期 10 年，暂无紧迫性，但项目化必须写清楚）。轮换需覆盖：EC+SAN 生成、密钥权限（User=hysteria → key 640 root:hysteria、cert 644）、客户端 CA 固定同步顺序（先分发新证书再切换）。

d) **备份/恢复**："备份脚本"一句不够，需明确备份范围（配置、单元、证书、二进制、订阅产物、/root 下 token 与 links 文档）和"新机还原"演练，后者同时检验 secrets 解密链路。

e) **凭证轮换章节**：方案没有 rotation 文档。skill 记录过 keypair/uuid 轮换后 clash.yaml、sub.txt、sub64.txt、subscription-links.txt、node-link.txt 全部要同批更新的教训，必须固化成文档 + 脚本自动全量更新。

f) **验收清单**：建议落成 scripts/test_health.sh 或 docs/acceptance.md，与第 5 节验收标准一一对应。

---

## 2. 凭证加密方案（评审点 2）

### 2.1 环境事实

- age、sops 均未安装；gpg、openssl 已装。
- 仓库目前无 remote，但不该假设永远私有；repo 一旦对外，*.enc 就是最后防线。

### 2.2 方案评估：对称 vs 非对称

**age 对称口令加密：可行，但不是最优。** 问题在于口令本身仍是一个必须存放、必须保护、必须备份的 secret——无论放 ~/.hermes/.env 还是 root-only 文件，都存在口令随环境变量传播、多人/多机协作时扩散的风险，且轮换等于换口令并重新加密全部文件。

**更优做法（建议采纳）：age 非对称 X25519。**

- 一次性生成 age identity（X25519 密钥对）；
- 私钥存 /root/secrets/age-identity.txt，权限 600，**仓库外、纳入备份**；
- 公钥可安全入库/入文档，加密用 `age -r <公钥>`，解密用 `age -d -i <私钥>`；
- 可同时给多个 recipient 加密（例如 Hermes 备份 key），实现密钥冗余；
- 吊销/轮换只动 recipient 清单，不需重发口令。

备选 sops（Mozilla）能对 YAML/JSON 结构化加密、git 友好，但多一个二进制依赖，本项目配置量小，age 足够，**不建议引入**。gpg 虽已装但 UX 差、密钥管理重，也不建议。

### 2.3 可靠性要求

1. identity（或对称口令）必须纳入备份并做恢复演练，否则 *.enc 全军覆没不可恢复；
2. 仓库内提供 decrypt-secrets.sh + secrets/README.md 字段清单，保证新机器 5 分钟内完成"解密 → 渲染 → 起服务"；
3. 部署脚本在解密后生成临时明文 .env，渲染完成后立即删除，不落仓库、不留中间产物；
4. 明文放 /root/secrets（仓库外）本来就不需要 gitignore；若任何人把明文放进仓库目录，靠 .gitignore 兜底（建议在 projects/proxy/.gitignore 显式禁止明文路径）。

### 2.4 威胁模型（诚实边界）

- age 保护的是"仓库内容泄漏"场景：拿到 repo 的人无法直接读明文。
- **不保护**：服务器被 root 入侵（identity 在同机）、运行时内存/日志泄漏、订阅产物本身公开（sub-server 无鉴权）。
- 结论：加密是纵深防御的一环，不能替代最小权限与审计，报告第 6 节单列相关风险。

---

## 3. 现有三个服务的迁移（评审点 3）

### 3.1 迁移本质与策略

三个服务端口固定且被占用（hysteria UDP 38475、xray TCP 443、sub TCP 8080），**同一端口无法新旧双进程并存**，因此不存在真正意义的"并行运行"。可行的并行是文件与单元层面的并行：

- 新建单元 proxy-hysteria.service / proxy-xray.service / proxy-sub.service + 项目内新配置路径，与旧单元并存但保持 disabled；
- 切换 = 备份 → 渲染 → 停旧 → 启新，脚本一步完成；窗口内订阅拉取闪断、客户端自动重连；
- 自动回滚 = 停新 → 恢复备份 → 启旧。

推荐新单元名 + 新路径（而非原地覆盖）：回滚干净、归属清晰、可随时对比新旧行为。若 Hermes 坚持原地覆盖，回滚只能靠恢复备份，风险更高。

### 3.2 迁移步骤（阶段 2 细化，此处给出评审要求）

1. **预检/盘点**：配置、单元、证书、二进制版本、订阅产物、/root 文档（本报告已完成初版盘点），生成基线备份；
2. **模板化**：三份配置改占位符模板，secrets 字段由解密后的 env 注入；
3. **渲染校验**：hysteria 配置以服务启动验证；xray 配置以服务启动验证；订阅产物用本地客户端回环验证；
4. **切换窗口**：脚本化停旧启新 + 健康检查，失败自动回滚；避开用户活跃时段（日志显示有常驻客户端）；
5. **观察期**：旧单元与备份保留 ≥2 周，确认无异常再清理。

### 3.3 必须进 secrets 的字段清单

| 服务 | 字段 | 性质 | 说明 |
|---|---|---|---|
| hysteria | auth.password | 连接凭据 | 与订阅产物同源同步 |
| xray | clients[].id（UUID） | 连接凭据 | 与订阅产物同源同步 |
| xray | realitySettings.privateKey | 服务端私钥 | **建议只存私钥，公钥(pbk)由脚本推导**，杜绝私/公不匹配 |
| xray | realitySettings.shortIds | 握手标识（半敏感） | 与私钥一起原子轮换 |
| sub | 订阅 token（现 /root/sub-token.txt，目录名即 token） | 订阅 URL 唯一防线 | http.server 无鉴权，token 泄漏 = 订阅公开 |
| sub | 链接文档中的完整订阅 URL | 内含 token | links 模板用占位符，不落明文 |

明确**不进仓库**：hysteria tls.key 文件本身（路径进模板，文件留在服务器）；server.crt 是公钥可公开，但建议也不入库（避免暴露证书指纹）。**非秘密**（放 .env.example）：服务器 IP、端口、masquerade URL、sni/CN、Reality dest 站点等。

### 3.4 迁移期顺手修正的现状问题

- xray config 当前 loglevel=debug → 项目化改 warning（减少日志噪音与 IO）；
- xray.service 当前以 root 运行、无 User=/NoNewPrivileges → 建议参照 hysteria 单元降权并裁剪 capability（注意配置/证书权限随降权调整，skill 有 hysteria key 权限踩坑记录）；
- 订阅目录防扫描弱，8080 已被扫描器探测（journal 中已有 POST /mcp、/sse 探测记录）→ 至少保持 token 长随机，是否加鉴权层由 Hermes 决策（见第 7 节）；
- 现有订阅里 hysteria2 节点用了 insecure=1，客户端模板应提供 CA 固定版本作为更优做法（行为变更需用户同意，不强推）。

---

## 4. 1GB 小机：脚本语言与依赖（评审点 4）

**结论：python3 stdlib 为主，age 静态二进制为唯一新增外部依赖。不引入 PyYAML、Jinja2、requests 等任何重依赖。**

理由：

- 渲染：xray 配置是 JSON → `json` 模块；hysteria 配置是 YAML → 用占位符文本替换（string.Template）即可，**不需要解析 YAML**，避免为解析而装 PyYAML；
- 订阅生成：纯文本拼接 + base64（stdlib），全部可测；
- 健康检查：subprocess 调 ss / curl / systemctl，stdlib 足够；
- age：Go 编译的静态单文件，装到 /usr/local/bin，运行时占用可忽略；
- 内存基线：现有三服务合计约 18MB（hysteria 5.2M / xray 7.8M / sub 4.7M），验证性/一次性脚本用 cron 或 systemd timer 跑，不做常驻进程。

部署脚本用 shell 写也完全可行，但涉及模板渲染 + 多产物同步时 python3 stdlib 的可读性与可测性更好；**选一种即可，不要两套并存**。

---

## 5. 回滚与验收（评审点 5）

### 5.1 回滚

- 原则：验收通过前旧服务一律保留；切换前生成带时间戳的全量备份（建议 /var/backups/proxy/，覆盖配置、单元、证书、二进制、订阅产物、/root 文档）；
- **自动回滚触发条件（任一）**：新单元启动失败或退出；健康检查任一项不通过；切换后 60 秒内外连异常；
- 手动回滚：rollback.sh = 停新 → 恢复备份到原路径 → 启旧 → daemon-reload → 复检；
- 观察期 2 周后才允许清理旧单元与备份。

### 5.2 验收标准（全部通过才算迁移成功）

1. **服务状态**：三个新单元 active (running) 且 enabled，Restart=on-failure；
2. **端口**：ss -ulnp 见 hysteria UDP :38475（UNCONN 属正常）；ss -tlnp 见 xray :443、sub :8080；
3. **Hysteria e2e**：服务器本地 hysteria 客户端（CA 固定）→ SOCKS5 → curl https://www.google.com 返回 200，api.ipify.org 出口 = 173.242.113.39；
4. **VLESS+Reality e2e**：本地测试客户端（mihomo/xray）→ curl 经代理成功；测试窗口内 xray journal 无 "invalid connection"；按 skill 的 X25519 校验法确认 privateKey/pbk 匹配；
5. **订阅三件套**：clash.yaml 可被 mihomo 加载；`base64 -d sub64.txt` 与 sub.txt 一致；URI 参数齐全；用 token URL 经公网 IP 拉取返回 200；
6. **凭证治理**：全仓库 git grep 无明文口令/UUID/私钥；secrets/*.enc 可解密且与线上一致；/root/secrets 权限 700、identity 600；
7. **安全基线**：xray 单元含降权配置（若采纳）；配置/证书权限满足 skill 记录的要求；xray loglevel=warning；
8. **恢复演练**：完成一次"新机还原"（解密 → 渲染 → 部署 → 起服务）并成功；备份 tarball 可恢复；
9. **客户端兼容**：真实客户端（Clash Verge 等）在凭据不变的前提下无改动仍可连接；若本轮决定轮换凭据，则需同步分发（Hermes 决策，见第 7 节）。

---

## 6. 其他风险点（评审点 6）

| # | 风险 | 可能性 | 影响 | 缓解措施 |
|---|---|---|---|---|
| 1 | age 未安装且 identity 未备份 → *.enc 不可恢复 | 中 | 高 | 部署脚本先装 age；identity 纳入备份与恢复演练 |
| 2 | 对称口令传播面过大 | 中 | 高 | 改非对称；私钥单文件 600 |
| 3 | 仓库历史泄漏（明文曾入库） | 低（新项目） | 高 | 首个 commit 前 .gitignore 与结构就位；若发生则 filter-repo 清史 + 全量轮换凭据 |
| 4 | 端口冲突 → 切换瞬间断连 | 必然（瞬时） | 低-中 | 脚本化停旧启新 + 自动回滚；避开活跃时段 |
| 5 | sub-server 无鉴权，token 泄漏即订阅公开 | 中 | 中 | token 入 secrets 且长随机；可选 nginx/caddy 加鉴权（待决策） |
| 6 | key/uuid 轮换漏同步订阅产物 | 中（曾发生） | 高 | 单一来源生成全部产物；rotation.md 清单；脚本全量同批更新 |
| 7 | hysteria 证书轮换未同步客户端 CA 固定 | 中 | 中 | rotation SOP；先分发新证书再切换 |
| 8 | Reality 依赖外部 dest（dl.google.com:443） | 低 | 中 | 预检出站 TLS1.3；dest 固定 IPv4；避开 www.microsoft.com（已知证书尺寸 bug） |
| 9 | xray 现以 root 运行 | 高（现状） | 中 | 迁移时降权 + capability 裁剪 |
| 10 | 服务器无全局 IPv6 | 已知 | 低 | 文档说明；dest/serverNames 全部 IPv4 可达 |
| 11 | 1GB 内存受限 | 低 | 低 | stdlib 一次性脚本；journald 限额（如 50M）防日志膨胀 |
| 12 | 时间漂移影响 Reality | 低 | 中 | chronyd 已运行，晨检脚本加 NTP 检查项 |
| 13 | 仓库无 remote、无异地备份 | 中 | 中 | 明确备份策略（tarball 异地或私有 remote），由 Hermes 定 |
| 14 | 现网有常驻客户端 | 高 | 低-中 | 短切换窗口 + 自动回滚 + 提前知会 |

---

## 7. 需要 Hermes 拍板的决策点

1. **加密形式**：采纳 age 非对称 X25519（推荐）还是维持对称口令？
2. **单元与路径**：新单元名 + 项目路径（推荐，回滚干净）还是原地覆盖复用？
3. **是否轮换**：本轮是否同时轮换订阅 token / 服务端凭据？轮换必须全量同步分发，影响所有现有客户端；
4. **sub-server 鉴权**：维持 http.server + 长 token，还是加 nginx/caddy basic auth？
5. **备份归属**：仓库建立私有 remote（异地备份），还是保持本地 + tarball 备份？

---

## 8. 阶段 1 输出范围声明

本评审仅创建了本报告文件（projects/proxy/docs/plan-review.md）；未创建 secrets、模板、脚本等任何实现文件，未改动现有任何服务或配置。
