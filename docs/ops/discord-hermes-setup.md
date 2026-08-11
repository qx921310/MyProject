# Discord + Hermes 接入最佳实践（2026-08-11 调研）

> 来源：hermes-agent.ai 官方教程、lucaberton 指南、r/hermesagent 完整指南、
> 翔宇跨平台实战（含踩坑）、GitHub issues、YouTube 教程。
> 为 koko 回国多 Agent 架构的 Discord 测试做准备。

## 一、完整流程（用户操作部分）

### 1. 创建 Bot（Discord Developer Portal）
- 打开 https://discord.com/developers/applications → New Application → 起名 → Create
- 左侧 Bot → Add Bot / Reset Token → **立即复制**（只显示一次）→ 保存好

### 2. 开启 Intents（最常见坑！）
- Bot 页面 → Privileged Gateway Intents → 三个都开：
  - **Message Content Intent（最关键）**——不开则 bot 能看到消息事件但读不到内容，回复"我无法理解你说什么"
  - Server Members Intent——访问成员列表（可选）
  - Presence Intent——在线状态（可选）
- 点 Save Changes

### 3. 生成邀请链接
- OAuth2 → URL Generator → Scopes 勾选 `bot` + `applications.commands`
- Bot Permissions：Send Messages、Read Message History、View Channels、Embed Links、
  Attach Files、Add Reactions、Create Public/Private Threads、Manage Threads
  （教程推荐权限整数 274878286912）
- 复制 URL → 浏览器打开 → 选服务器 → 授权

### 4. 获取用户 ID / 频道 ID
- Discord 设置 → 高级 → 打开开发者模式
- 右键自己头像 → Copy User ID
- 右键频道 → Copy Channel ID（可选，做 home channel）

## 二、Hermes 配置（我来做）

```bash
# ~/.hermes/.env 加：
DISCORD_BOT_TOKEN=<token>
DISCORD_ALLOWED_USERS=<用户ID>    # 个人 bot 推荐
# 或 DISCORD_ALLOWED_ROLES=<角色ID>   # 团队用
# 或 DISCORD_ALLOWED_CHANNELS=<频道ID> # 限定频道
```

**fail-closed 铁律（2026 变更，多数教程没提）**：
- 只配 `DISCORD_BOT_TOKEN` 不够！**必须**配至少一个访问策略
- 否则 bot 看起来在线，但所有用户消息都被拒绝（fail-closed）
- **优先用 DISCORD_ALLOWED_USERS/ROLES**，不要用 `DISCORD_ALLOW_ALL_USERS=true`
  （那会让任何能看到 bot 的人都能跟你 agent 说话）

配置后重启 gateway，发一条真实消息验证。

## 三、踩坑清单（社区实战总结）

### 坑 1：Message Content Intent 没开
- 症状：bot 在线，看到消息但不理解内容
- 解法：开发者门户开 intent（见上）

### 坑 2：fail-closed 导致"无人能说话"
- 症状：bot 在线但任何消息无响应/被拒
- 解法：配 DISCORD_ALLOWED_USERS

### 坑 3：⚠️ Discord DM 发送失败（GitHub issue #22882）
- 症状：`send_message(target="discord:<user_id>")` 报 `Unknown Channel (404)`
- 原因：Hermes 的 Discord 适配器用 user_id 直接发 DM，但 Discord API 要求先用
  `POST /users/@me/channels` 拿 DM channel id
- **对咱们的影响**：以后 cron 推送若目标是"私聊用户"可能失败
- **Workaround**：① 建个私密频道当 home channel，推送发到频道（群聊正常）；
  ② 或手动调 Discord REST API 先开 DM 再发
- **结论：推荐推送目标用"频道"而非"私聊"**——正好符合多 Agent 群聊方案

### 坑 4：双 Bot 冲突
- 同服务器同时有 Hermes + 其他 AI bot（如 OpenClaw），每条消息俩都抢答
- 解法：`require_mention: true`（只 @提及才响应）或不同 bot 放不同频道

### 坑 5：Token 安全
- token 视为密码：绝不提交 git、不发公共渠道
- 泄露立即 Reset Token
- 我收到后会存 `.env`（敏感文件，不入库）

## 四、对本机/回国架构的适配建议

1. **推送目标用频道**（避开 #22882 DM 坑）——建一个"简报"私密频道当 home channel
2. **多 Agent 分工**：主 Hermes 一个频道、小龙虾另一个频道（避免双 bot 抢答）
3. **排版**：Discord 支持完整 markdown + embed——早盘简报可用
   `- 📈 上证指数 3954.94（-0.29%）` 列表 + 可选 embed 卡片增强
4. **fail-closed 配置**：DISCORD_ALLOWED_USERS=koko 的 Discord ID（其他人不能指挥 bot）
5. **回国后**：Discord 需要梯子（搬瓦工自备）；飞书免梯子仍是主通道，
   Discord 作为排版增强/备份通道
