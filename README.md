# 小念 XiaoNian — 你电脑里会成长的生活助理

> 一个本地优先、隐私安全的个人生活助理 / AI 秘书：住在你电脑里，越用越懂你、会主动关心你、
> 能帮你在电脑上把活干完，并且可以按需安装 Skill 自我进化。

核心只有四件事，保持精简稳定；一切"杂"能力都做成可插拔 Skill——**功能不杂，但能无限生长**。

| 能力 | 说明 | 底座 |
|---|---|---|
| 懂你 | 长期记忆：画像 / 生活状态 / 习惯 / 关系，越用越懂你 | **Mem0** + 本地隐私嵌入器 |
| 主动关心 | 在对的时间主动问候、提醒、关心（非被动等指令） | APScheduler + 预测触发 + 静默协议 |
| 干活 | 真正在你电脑上把活干完（文件 / 命令 / 写东西 / 浏览器） | 受控 harness（可选 Open Interpreter）|
| 进化 | 按需装技能扩展能力（外卖 / 打车 / 周报…） | **Anthropic Agent Skills** 开放标准 |

工程上**企业框架优先**：编排 **LangGraph**、记忆 **Mem0**、技能 **Agent Skills**、
模型多 provider 可切换（**MiniMax M3 / DeepSeek / 小米 MiMo**，全 OpenAI 兼容）。

## 快速开始

```bash
# 1) 需要 Python ≥ 3.10（推荐 3.12）
python3.12 -m venv .venv && source .venv/bin/activate

# 2) 安装依赖
pip install -r requirements.txt

# 3) 配置密钥（复制示例并填入）
cp .env.example .env   # 填 MINIMAX_API_KEY / DEEPSEEK_API_KEY 等

# 4) 启动
python run.py
# 浏览器打开 http://127.0.0.1:8765
```

> 没有可用模型额度时，小念会进入**离线降级演示模式**：意图识别 + 工具调用 + 两步确认 +
> 技能 + 记忆 + 宠物，整条链路依然可完整演示；配置好额度后自动切换为真实大模型。

## 跑评测

```bash
python -m eval.runner   # 真实生活场景端到端成功率
```

## 架构

```
交互层(本地 Web UI + 宠物)
        │
   LangGraph 编排  ──►  安全链(两步确认 / PII脱敏 / 审计)
        │                         │
   记忆 Mem0(本地)          能力层(电脑操控 / Skill系统 / 宠物)
        │
   模型层(多 provider: MiniMax / DeepSeek / MiMo)
        ▲
   主动关心引擎(cron + 预测 + 静默)
```

## 目录结构

```
xiaonian/
  daemon/        # 本地 daemon (FastAPI) + 生活状态推断
  graph/         # LangGraph 编排：状态/节点/工具/人格
  memory/        # Mem0 集成 + 本地隐私嵌入器 + 结构化画像
  capabilities/
    computer/    # 电脑操控 harness（读即时 / 写两步确认）
    skills/      # Agent Skills 系统 + 3 个示范技能
    pet/         # 宠物：状态→情绪映射
  proactive/     # 主动关心引擎（cron + 预测 + 静默协议）
  security/      # 两步确认 / PII 脱敏 / 审计日志
  observability/ # token/成本/trace 本地追踪
  model/         # 多 provider 抽象（OpenAI 兼容 + 离线降级）
  web/static/    # 本地 Web 前端（对话/宠物/记忆/可观测）
  eval/          # 场景评测套件 + runner
  docs/adr/      # 架构决策记录（ADR）
```

## 隐私

记忆、画像、审计、trace、凭证全部存于本机 `data/`，默认不出域；记忆嵌入在本机完成；
上云仅发经 PII 脱敏的最小上下文。详见 `docs/adr/0002-local-first-privacy.md`。

## 部署

本地个人版 / 服务器企业版 / 生态版三种模式，见 `docs/deployment.md`。

## 背书与参考

- 编排 LangGraph：Klarna / Uber / 摩根大通生产使用
- 记忆 Mem0：业界领先的 Agent 记忆层（亚秒级、可本地）
- 技能 Agent Skills：Anthropic 2025 开源的开放标准，Atlassian/Figma/Canva/Stripe/Notion 采用
- 两步确认：借鉴美团 DPT-Agent 的人在环范式；评测借鉴美团 VitaBench 真实场景成功率思路
