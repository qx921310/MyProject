# 钻仔会话卡死治理方案（合并版 v2，采纳 Codex 复审意见）

> 创建：金仔 2026-08-13 21:26（北京时间）
> 复审：Codex 2026-08-13 21:30（结论：需修改，本版已全部采纳）
> 背景：钻仔（OpenClaw，首尔 43.155.129.186）近两日群会话反复卡死 running，dispatch 全挂导致"装死"。08-12 一次、08-13 两次（12:46、18:20），重启无效，必须清会话引用才能恢复。
> 原则（KOKO 定）：**去掉补丁类人为干涉，能走配置的走配置**。

## 一、问题根因（已确认事实）

1. **症状链**：群会话在 `session_nodes`/`session_windows` 卡在 `running` → 新消息派发报 `Session changed while starting work. Retry.` → 无限重试失败 → @ 不响应（装死）。
2. **重启治不好**：卡死状态持久化在 sqlite（`agents/main/agent/openclaw-agent.sqlite`），systemd 重启只重启进程不清 DB。
3. **触发卡死直接原因未确凿定位**：线索指向高活跃大群 + 超长上下文 + 会话锁未释放。需要监控数据验证。

## 二、方案组成（三个子方案合并，v2 修改点以 ⭐ 标注）

### A. 会话健康监控（治"发现晚"）

- **功能**：定时检查会话状态：
  - ⭐ **告警条件改为活动性判断**：`status='running'` **且连续 30 分钟无任何活动**（transcript/updated_at/last_activity 均早于阈值）才告警，避免正常长任务误报
  - ⭐ **告警冷却**：同一会话 1 小时内不重复告警；状态恢复后发"已恢复"通知
  - 统计 dispatch 错误（journalctl `changed while starting work` 计数）超阈值告警
  - ⭐ **只读连接**：Python 用 `mode=ro` URI 打开 sqlite，不干扰运行中 gateway
  - ⭐ **优先官方指标**：先检查 OpenClaw 自带 Prometheus 指标 `openclaw_session_recovery_total` / `openclaw_session_recovery_age_seconds` 是否可用，能省掉部分手工解析
- **部署**：脚本放首尔 `/opt/openclaw-tmp/scripts/`，cron 每 5 分钟一次；告警走飞书群 @KOKO（webhook + open_id 放首尔 env，**不入库**）
- **阈值可配置**（config 文件，非硬编码）

### B. 一键修复脚本（治"修复慢"）

- **流程**（v2 调整）：
  1. ⭐ **官方命令优先**：先探测 `openclaw sessions list --json` / `delete --dry-run` 可用性，可用则优先官方命令
  2. 备份 DB（带时间戳 `openclaw-agent.sqlite.bak-<ts>`，保留 7 天）
  3. 停服 `systemctl stop openclaw-gateway.service`
  4. ⭐ **动态清理兜底**：脚本动态枚举 sqlite 全部表，找出含 session_id/session_key 列的表，清理目标会话的引用（不硬编码表清单，杜绝漏表外键违规）；必须覆盖：session_nodes / session_windows / session_conversations / transcript_events / session_transcript_active_events / session_transcript_index_state / session_transcript_fts / transcript_event_identities / transcript_rewrite_watermarks / trajectory_runtime_events
  5. 保留 `conversations` 主会话种子（重建依据）
  6. 校验：`PRAGMA foreign_key_check` 必须返回 0，否则中止并提示回滚
  7. 重启并验证：journalctl 无新 dispatch 错误 + ⭐ `openclaw sessions list` / `openclaw health` 确认会话与渠道恢复
- **注意**：清理必须在**停服状态**（gateway 运行时会写回残留导致外键违规，08-13 实测）
- **安全**：默认 dry-run，人工确认加 `--execute` 才真删；备份先行；异常中止可回滚

### C. 根因追踪（治"复发"）

- **快照字段**（v2 补充）：
  - session_key / session_id / status / transcript 事件数 / 上次活动时间
  - ⭐ `openclaw --version` + `PRAGMA user_version`（DB schema 版本）
  - ⭐ journalctl 错误原文抽样（最近 3 条 dispatch 错误）
  - ⭐ 是否出现 pendingDeliveryNotice / tombstone 标记
- **共享 state 库**（`~/.openclaw/state/openclaw.sqlite`）：与卡死会话关联的 delivery/approval/restart sentinel 跨库不查外键，**根因阶段只观察记录、不擅自清理**
- **预防性走配置**（⭐ 根因阶段评估后启用，正对疑似根因）：
  - `session.reset.mode=idle`（闲置自动重置）
  - `session.maintenance.pruneAfter` / `session.maintenance.maxDiskBytes`（限制上下文规模）

## 三、补丁治理（KOKO 原则落地，v2 更新）

| 现有补丁/hack | 处理（v2） |
|---|---|
| 英文兜底通知补丁（OpenClaw dist `deliverPendingDeliveryNotice` 插 return） | ⭐ Codex 查证：禁用的只是英文提示文本，不影响 OpenClaw 自带自动重启恢复逻辑；官方暂无明确配置项替代。**处理：保留但文档化，不急着移除**（移除会让英文兜底通知回来）；跟踪上游是否出配置项 |
| 其他源码级补丁 | 一律评估能否走配置；能走配置的迁移，不能的文档化并标注维护责任 |

## 四、实施步骤（Codex 建议顺序）

1. ✅ 方案复审（Codex，已完成，本版已采纳意见）
2. ⭐ **首尔实测（钻仔配合、只读）**：记录实际 OpenClaw 版本与 `PRAGMA user_version`；验证 `openclaw sessions list --json` 和 `delete --dry-run` 可用性；确认部署 schema 是否含缺失表
3. **实现监控脚本**（只读 + 活动性阈值 + 冷却 + 快照日志）→ requesting-code-review → 提交 hermes-main
4. **实现一键修复脚本**（官方命令优先 + 动态 sqlite 兜底 + dry-run 默认 + 备份 + 回滚）→ review → 提交
5. **部署首尔** `/opt/openclaw-tmp/scripts/`（钻仔确认；webhook/open_id 走首尔 env 不入库）→ 人工 dry-run + 观察监控首轮输出
6. **积累 2-3 次卡死快照**后用数据定根因 → 再决定启用 `session.reset`/`maintenance` 配置项 → 英文兜底补丁最终处理（保留文档化或等上游）

## 五、风险与边界

- 改钻仔（首尔）配置前必须钻仔同意（协作边界铁律）
- 一键修复脚本权限放大：dry-run 默认 + 备份先行 + 校验中止
- 监控告警频率控制（5 分钟检测 / 1 小时冷却），避免刷屏
- 不引入本地重服务（首尔 1GB 内存约束）
- 敏感信息（webhook/open_id）一律入首尔 env，不入库
- 仓库是唯一事实源，部署为复制；本机 cron 任务遵守 `~/.hermes/scripts` 约定
