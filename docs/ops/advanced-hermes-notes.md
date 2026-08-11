# 高级 Hermes 学习笔记（2026-08-11 午休进修）

> 来源：官方 Tips & Best Practices、r/hermesagent 社区 use cases、PM 实战指南（aakashg）、Felo/Vectorize 社区分析。
> 目的：从社区成熟经验提炼可落地的改进，让 Hermes 真正为主人分担压力。

## 一、官方最佳实践（可立即执行的）

### 1. 上下文前置（减少来回）
- 请求里直接给：文件路径、错误信息、预期行为——一次说清省三轮澄清
- 贴错误 traceback 让 agent 自己解析

### 2. 规则用 Context Files，不进记忆
- 重复指令 → AGENTS.md（项目级，自动加载）
- 人设 → SOUL.md（全局）
- 记忆只存"是什么"，技能存"怎么做"

### 3. 性能铁律
- **别频繁切模型**——破坏 prompt cache，后续全价重读
- 长会话用 `/compress` 前先让 agent 记忆关键点
- 并行研究用 `delegate_task`，批量操作用 `execute_code`

### 4. 技能创建时机
- 任务 5+ 步且会重复 → 立即存技能（"save what you just did as a skill"）
- 社区数据：同一任务从 20 分钟 → 8 分钟（6 周，技能自我重写 4 次）

## 二、社区真实 use cases（r/hermesagent，可借鉴）

1. **聚合监控**：定时拉 Reddit/X/LinkedIn 相关话题，汇总成笔记（6 次/天）→ 我们可做：竞品/行情相关资讯聚合
2. **每日 smoke test**：开工前自动测试自己产品 → 我们可做：服务器晨检（服务状态+内存+日志）
3. **家庭协调**：日历、提醒（"电影上映提醒我"）→ 我们可做：关键日期提醒（CPI、财报、家人重要日子）
4. **知识库**：转发的链接自动进 LLM wiki → 我们已有 CodeHub + Holographic，可强化
5. **工作流自进化**：技能会自我重写（4 次/6 周）→ 依赖 Curator + 每日复盘

## 三、对咱家的落地清单（按优先级）

### 🔥 P0：立即能做（今天）
1. **服务器晨检日报**：每日简报前先跑健康检查（服务/内存/磁盘/日志异常），异常自动附在简报里
2. **关键日期提醒**：把 CPI/美联储议息/财报/家人重要日期做成 cron 提醒
3. **技能自我进化检查**：确认 Curator 每周跑 + 每日复盘 22:00 正常

### 📌 P1：本周做
4. **资讯聚合监控**：每周自动搜行情/技术圈相关话题汇总（复用 competitor-news-monitor）
5. **AGENTS.md 完善**：给 CodeHub 之外的关键目录（~/.hermes/scripts/）建规范

### 💤 P2：等便宜 VPS
6. **Hindsight 长期记忆**（要 PostgreSQL，1GB 跑不动）
7. **mission-control 多 Agent 编排**（小龙虾来了再说）

## 四、与小龙虾（OpenClaw）对比启示
- 社区共识（Kilo 分析 1300 条 Reddit 评论）：
  - Hermes = 更好的自学习 runtime；OpenClaw = 更好的控制面
  - 20% 用户两者同用：OpenClaw 当 orchestrator，Hermes 当执行专家
- 启示：不用"二选一"，可以"分工协作"——咱们本来就是这个路线

## 五、关键教训
- "Be specific" 是双向的：主人给明确指令，Hermes 给明确汇报
- 记忆是有边界的（2200 字符是设计），靠技能 + 外部 Provider 扩展
- 社区的"自进化"核心 = 技能会自我重写，不是模型变强，是流程固化
