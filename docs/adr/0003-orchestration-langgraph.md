# ADR-0003：编排用 LangGraph + 运行环境用 Python 3.12

- 状态：已采纳
- 日期：2026-06-30

## 背景
小念的大脑需要：多步推理、工具调用循环、人在环（写操作确认中断/恢复）、多轮状态持久化。

## 决策
- 用 LangGraph 的 StateGraph 实现状态机：`recall → agent →(工具?)→ tools → agent … → persist → END`，
  用 MemorySaver 做 checkpointer 支持多轮线程状态。相比 CrewAI，LangGraph 提供
  "精确控制 + 可调试 + 生产级持久化"，更适合管家这类需要可靠落地的场景。
- 运行环境：Python 3.12。Mem0 2.0 在运行期使用了 3.10+ 的类型联合语法，
  Python 3.9 会在构建 Memory 时报 `unsupported operand type(s) for |`。
  评估后选择以 3.12 venv 运行，让 Mem0 原生跑通（而非降级或改源码）。

## 结果
- 正：编排可控、可观测、可恢复；Mem0 原生工作。
- 负：要求部署环境 Python ≥ 3.10（已在 README 写明）。
