---
repo: openai/codex
display_name: Codex
last_sha: 544b4e39e3aba5990f9af42a3381695927695f4b
last_built: 2026-04-19T17:36:32Z
model: claude-opus-4-7
---

## 概览

Codex CLI 是 OpenAI 官方的本地编码 agent——以单个原生二进制安装到用户机器（`npm i -g @openai/codex` 或 `brew install --cask codex`），交互式 TUI 和无头 `codex exec` 两种主要形态。登录走 ChatGPT 账号（Plus/Pro/Business/Edu/Enterprise 额度复用），也支持 API key。

解决的不是"给你代码补全"，而是"让一个 agent 在你机器上读文件、跑命令、改代码、打 PR"——同时把爆炸半径框在一套可配置的沙盒 + 网络策略里。

和 Claude Code 是最直接的同类对标：都是本地 agent + TUI + IDE 插件 + MCP 双向支持的形态。差异主要在后端模型（GPT-5.1/5.2 系列 codex 模型，prompt 直接放在 `core/` 里）、沙盒实现（自研跨平台：macOS Seatbelt / Linux Landlock+bwrap / Windows Sandbox）、以及 IDE 协议（自研的 `codex app-server` JSON-RPC，不是 LSP）。

是生产级——已有 VS Code/Cursor/Windsurf 插件、TypeScript + Python SDK、独立的"Codex Web"云端 agent 形态（`chatgpt.com/codex`，和本 repo 不是同一个产品）。这个仓库专注本地 CLI + IDE 集成。

## 架构

仓库是一个 monorepo，主产物由 Rust 实现（`codex-rs/`），遗留 TypeScript CLI (`codex-cli/`) 已不再是主线。`codex-rs/` 本身是一个 60+ crate 的 Cargo workspace。

**顶层入口与形态**：
- `cli/`——多工具入口，`codex` 命令的主体；所有子命令（tui / exec / app-server / mcp / mcp-server / sandbox / debug）的分发点
- `tui/`——基于 Ratatui 的全屏 TUI，默认交互形态
- `exec/`、`exec-server/`——无头模式，用于 CI、脚本、SDK 后端
- `app-server/`、`app-server-protocol/`、`app-server-client/`——为 IDE 扩展（VS Code 等）服务的 JSON-RPC 2.0 服务端，支持 stdio 和 websocket 传输
- `mcp-server/`、`codex-mcp/`、`rmcp-client/`——MCP 双向：Codex 既是 MCP client（连别家 MCP server），也能 `codex mcp-server` 把自己暴露为 MCP 工具
- `cloud-tasks/`、`cloud-tasks-client/`——把任务下发到 Codex 云端 runtime 的通道

**核心业务逻辑**：
- `core/`——`codex-core` 是最大也最臃肿的 crate，承载 agent 主循环、rollout、工具调用、apply_patch、plugin/skill/memory/hook/connector 的落地实现。仓库自己 AGENTS.md 明确写了"resist adding code to codex-core"
- `protocol/`、`codex-api/`、`codex-client/`、`backend-client/`——协议与后端访问
- `model-provider-info/`、`models-manager/`、`ollama/`、`lmstudio/`——模型供应侧，官方 API 外还接 Ollama / LM Studio 本地模型
- core 里的 `gpt-5.2-codex_prompt.md` / `gpt-5.1-codex-max_prompt.md` 等——Codex 主模型的 system prompt 直接以 markdown 形式签进仓库

**平台安全层**：
- `sandboxing/`——跨平台沙盒管理层，内含 Seatbelt SBPL 策略、Landlock + bubblewrap 组合、策略转换
- `windows-sandbox-rs/`、`linux-sandbox/`——平台专有实现
- `network-proxy/`——本地强制流经的 HTTP(3128) + SOCKS5(8081) 代理，按 allow/deny 策略拦截
- `execpolicy/`、`process-hardening/`、`shell-escalation/`——命令执行前的策略判定与权限提升
- `keyring-store/`、`secrets/`、`login/`——凭据存储，平台级密钥环抽象

**能力扩展**：
- `skills/`、`core-skills/`——Skill 定义与运行时
- `core/src/plugins/`——Plugin 管理器 + 本地/远程 marketplace
- `hooks/`、`connectors/`——生命周期 hook 与第三方连接器
- `core/src/memories/`——两阶段记忆管线（见词典）

**SDK 层**：
- `sdk/typescript/`、`sdk/python/`——两个 SDK 都是 `codex` CLI 的 subprocess 包装，基于 JSONL 事件流。不是直接调 API，而是复用本地 CLI 的全部能力（沙盒/skill/memory 等）

数据流主干：用户输入 → TUI/app-server/exec 入口 → `codex-core` agent 循环（调模型、收工具调用）→ 工具执行前过 execpolicy/sandboxing/network-proxy → rollout 落盘 → 事件回流到入口层。

## 概念词典

- **rollout**：一次 Codex 会话的完整落盘产物（所有 item + 元数据）。默认写到磁盘，供后续 resume / memory 提取使用。`codex exec --ephemeral` 关闭持久化。
- **Thread / Turn / Item**：app-server 协议里的三级原语。Thread 是一整段对话；Turn 是一轮用户→agent 收尾；Item 是最细粒度的事件（用户消息、reasoning、shell 命令、文件编辑等）。IDE 插件和 SDK 都按这个模型流事件。
- **app-server**：Codex 的 IDE 接入协议实现。JSON-RPC 2.0，stdio 或 websocket 传输，和 MCP 很像但是 OpenAI 自研——VS Code/Cursor/Windsurf 插件就是它的客户端。
- **sandbox_mode**：顶层权限档位。三选一：`read-only`（默认，只读）、`workspace-write`（可写当前 workspace，无网络）、`danger-full-access`（关闭沙盒，仅在容器等隔离环境用）。
- **Seatbelt**：Apple 的 `sandbox-exec` 工具，基于 SBPL（.sbpl 策略文件）。Codex 在 macOS 上的沙盒用它，策略文件在 `sandboxing/src/seatbelt_base_policy.sbpl`。
- **Landlock**：Linux 内核提供的 unprivileged 文件访问控制机制，按文件系统路径授权。Codex 的 Linux 沙盒基于它。
- **bubblewrap / bwrap**：Linux 上的轻量 namespace 沙盒工具。Codex 把 Landlock 和 bwrap 组合用，拿到文件+进程双重隔离。
- **execpolicy**：命令执行前的策略判定层。`execpolicy` 是新实现，`execpolicy-legacy` 是旧版——说明这一层最近在被改写。
- **network-proxy**：Codex 启动时拉起的本地 HTTP+SOCKS5 代理（默认 3128/8081），强制 agent 发起的网络流量走它，按 config 里的 allow/deny 和 "limited" 模式拦截。是"网络权限"的实际执行点。
- **Skills**：Codex 的可复用能力单元，类似 Claude 的 skills。由 `skills/` crate 承载，用户可自己加。
- **Plugins / Marketplace**：比 skill 更重的扩展机制，`core/src/plugins/` 里有 manager、manifest、本地和远程 marketplace 的实现。
- **Memories pipeline**：两阶段后台记忆管线。Phase 1 对每个 rollout 单独跑 LLM 抽取 `raw_memory` + `rollout_summary`；Phase 2 单例消费所有 Phase 1 产物，跑一个专门的 consolidation sub-agent 更新 `~/.codex/memories/` 下的 `raw_memories.md` 和 `rollout_summaries/`。会话启动时异步触发。
- **MCP client vs mcp-server**：容易混淆。`codex mcp` 管的是 config.toml 里的 MCP server 启动器（Codex 作为 client 去连）；`codex mcp-server` 是把 Codex 自己作为 MCP server 跑起来，让别家 agent 把 Codex 当工具调用。
- **`@openai/codex` vs `@openai/codex-sdk`**：前者是 CLI 本体；后者是 SDK，是 CLI 的 subprocess 包装器——SDK 内部 spawn `codex` 进程、按 JSONL 通信。
- **Codex Web**：`chatgpt.com/codex` 上的云端 agent，和本仓库的本地 CLI 不是同一个产品；README 第一屏就在撇清这个混淆。
- **CODEX_SANDBOX / CODEX_SANDBOX_NETWORK_DISABLED**：环境变量。当 Codex 把子进程丢进沙盒时会设上，测试代码会检查它来早退——AGENTS.md 明确禁止任何 PR 修改这两个变量相关的代码。
- **ChatGPT sign-in**：Codex 特色的登录形态。不是传统 API key，而是复用用户 ChatGPT 订阅额度——Plus/Pro/Business/Edu/Enterprise 都包。走 OAuth-like 流程，凭据落地到 `login` crate + keyring。

## 读者画像

主要用户：**日常在终端里写代码的工程师**，尤其是三类：

1. **ChatGPT 付费订阅用户**——已经在 Plus/Pro 上有额度，想把同一个订阅伸到本地 shell 里；
2. **VS Code / Cursor / Windsurf 用户**——通过官方插件把 Codex 嵌进编辑器；
3. **把 agent 接进 CI / 脚本的团队**——用 `codex exec` 或 TypeScript/Python SDK 把 Codex 变成自动化流程里的一个节点。

不太合适：**没有 ChatGPT 订阅且不想走 API key 的用户**、**需要在非 macOS/Linux/Windows 平台上运行的用户**、**要求 agent 产品与 OpenAI 生态完全解耦的团队**（虽然能接 Ollama/LM Studio 本地模型，但核心 prompt、后端协议、skill 生态都是围绕 OpenAI 模型设计）。

## 跟踪焦点

从 workspace 结构和文档能看出几条主线：

**1. 执行与沙盒层持续硬化。** `execpolicy`（新）和 `execpolicy-legacy`（旧）并存，说明命令执行的策略判定最近在重写。`process-hardening`、`shell-escalation`、`windows-sandbox-rs`、`sandboxing` 各自独立成 crate——这是一个把安全边界不断往细颗粒切的信号。Windows 沙盒独立成 crate 尤其值得跟——Windows 平台的 agent 沙盒比 Unix 难做得多，这块的稳定度是 Codex 能不能在 Windows 企业环境里推广的关键。

**2. app-server 协议稳步取代 LSP 式集成。** IDE 插件生态（VS Code/Cursor/Windsurf）都通过 `codex app-server` 的 JSON-RPC 协议接入，不用 LSP 也不复用 MCP。协议本身还标着 "Experimental API Opt-in" 节——说明还在演进。有 websocket 传输在实验阶段（被明确标 **experimental / unsupported**），要跟的是它会不会稳下来、会不会被用来做远程/多租户场景。

**3. Memories + Skills + Plugins 三条扩展线同时在推。** Memories 是两阶段后台管线（phase1 每会话抽取、phase2 全局 consolidation），Skills 和 Plugins 各成 crate 且有 marketplace 概念。短期关注：Plugin marketplace 什么时候开放远程 feed、Skill 是否会有公共注册表、Memories 的 consolidation prompt 效果如何。

**4. SDK 策略明确是"CLI subprocess"而不是"API 重实现"。** TypeScript 和 Python SDK 都是 spawn `codex` 进程然后 JSONL 通信。这个路线意味着 SDK 永远跟着 CLI 走，但也意味着 SDK 没法脱离本地 CLI 独立分发——对服务端部署场景是个限制。

**已知结构性问题**：`AGENTS.md` 自己点名 `codex-core` crate 已经过大，明确要求新代码别往里塞；`tui/src/app.rs`、`chatwidget.rs`、`bottom_pane/footer.rs` 等文件被列为"已经太臃肿、会吸引无关改动"的高风险文件。这是一个持续的内部重构压力，PR 里能频繁看到"把 X 从 core 拆出来"这种动作。
