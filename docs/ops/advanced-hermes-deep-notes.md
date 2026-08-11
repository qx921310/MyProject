# 高级 Hermes 深度学习报告（第二轮·2026-08-11）

> 来源：r/hermesagent "Power Users" 实战帖、Top 1% 用户"One month with Hermes"、NanoSkill 技能设计指南
> 这一轮学到的才是"高级 agent"的真正内核——不是功能清单，是架构思维。

## 一、高级架构思维（社区核心共识）

### 1. 多 Agent 流水线（研究/情报场景最佳实践）
资深用户（ivanzhaowy）的研究工作流——**拆成 3 个专业 agent**：
- collector（采集）：papers/GitHub/docs/release notes
- verifier（验证）：检查来源、日期、API 变化、仓库活跃度
- synthesizer（综合）：写摘要、标注不确定性

**核心洞察**：不是"一个全能 agent 干所有事"，而是"流水线分工，各司其职"。
**输出格式不是"summary"**，而是：what changed / why it matters / source links / confidence / next action——**可行动、可验证、有置信度**。

→ 我们的行情简报其实是"collector(脚本采集) + synthesizer(AI 分析)"，缺了 verifier 环节（数据可信度校验）。

### 2. Profiles 是设计工具，不是便利功能（Top 1% 用户核心观点）
- "不要把默认 profile 变成装满一切的大背包"
- **一个 profile = 一个角色**：coding / research / automation / writing 分开
- 每个 profile 有独立 config / SOUL / memory / sessions / skills / cron
- 建立方法：`hermes profile create`，然后 clone 现有 config 裁剪
- **关键原则**：一个 profile 一个工作，一个记忆空间，一套干净工具
- 注意：profiles 隔离状态但不隔离文件系统——coding profile 要显式设 `terminal.cwd`

→ 对我们的启示：**我们目前是"全能管家"单 profile**，符合"云管家"定位（需要跨领域），但如果 EVA/koko 场景分化，可以考虑 profile 分离。

### 3. Config 就是产品本身（"Hermes 表现怪 = 配置问题"）
- "很多 'Hermes acting weird' 的时刻，其实是配置时刻"
- 错误假设、缺失设置、同时激活太多东西、不理解参数权衡
- **方法**：让 Hermes 解释自己的 config——"ask it to explain your config"
- 不要盲目 tweak，先问"什么可能造成这个行为"

### 4. 技能系统是核心，不是配件
- "你不仅是在提示一个 agent，你是在塑造一个运行环境"
- 技能是 runbook（操作手册）：输入 / 步骤 / 输出 / 失败恢复
- **技能评判标准**（NanoSkill rubric）：
  - 结果清晰（产出具体交付物）
  - 输入明确（显式问 URL/关键词/约束）
  - 渐进披露（只加载需要的）
  - 失败处理（解释常见错误和下一步）
  - 可维护（版本化、可更新）
  - 安全权限（凭证/审批/工具访问清晰）
- **黄金标准**："如果技能不能被描述成你凌晨 2 点也信任的 runbook，它就不适合生产"

### 5. 成长方式（"Hermes 不是完成的设置，是学习如何成长的系统"）
- 别第一天就建整个机器——**一个工作流做扎实，再加下一个**
- 修坏掉的工作流 = 让原始想法更清晰
- 技能维护：窄技能、真实失败后更新、版本化、结构化输出、审查自动生成、去重
- 定期整理：把项目状态/决策/进度存成"master memory file"（Obsidian/知识库）——跨 profile 共享知识的方式

## 二、对我们的落地建议（按价值排序）

1. **给简报加 verifier 环节**——行情数据采集后先校验（多源交叉对比已有！），新闻源标注置信度——部分已实现
2. **Profile 分离评估**——如果 EVA 要独立场景（比如只做聊天/图像），建独立 profile；koko 云管家保持全能
3. **技能质量审查**——用 NanoSkill rubric 过一遍我们现有的核心技能（market-data-briefing、hermes-gateway-ops），补齐"失败处理"和"输入说明"
4. **知识库强化**——参考"master memory file"思路，把重要项目状态固化到 CodeHub 文档（而非只靠记忆）
5. **让 Hermes 解释 config**——遇到异常先自诊断配置，不盲目改

## 三、自我改进承诺（今日起执行）
- 技能创建时自带"失败处理"章节（Pitfalls + 下一步）
- 重要工作流先小规模跑通再扩大
- 遇到"表现怪"先查配置再动手
- 每周末用 rubric 审查技能库（可在每日复盘任务里加一条）
