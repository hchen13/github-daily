---
repo: openai/codex
display_name: Codex
last_sha: 6bbd710496e2465f9004c8f2c1aa3c9f50ea1ff1
last_built: 2026-04-22T00:09:44Z
model: claude-sonnet-4-6
---

I now have enough context to write the wiki. Let me produce the document.

---

## 概览

Codex CLI 是 OpenAI 官方出的一个本地运行的 AI 编程 agent。核心用途：在你的终端里打开一个全屏 TUI（或用 `codex exec` 跑无人值守模式），让 GPT-5 系列模型帮你读、写、调试本地代码——shell 命令在沙盒里执行，用户随时可以审批或拒绝。

解决的痛点：GitHub Copilot/Cursor 之类的工具都绑定在编辑器里，Codex CLI 是"纯终端+任意编辑器"的路子，同时支持非交互模式（`codex exec`），可以接进 CI/脚本管道。

与同类的区别：自带三层沙盒（macOS Seatbelt、Linux bubblewrap+Landlock、Windows 专有沙盒），命令执行前先过 execpolicy 规则引擎，不像很多竞品直接 `eval`。同时支持作为 MCP server 被其他 agent 调用（`codex mcp-server`），也支持连接 MCP server 扩展工具集。

当前阶段：生产级，已上 ChatGPT Plus/Pro/Enterprise 套餐（用 `Sign in with ChatGPT`），也支持直接传 API key。代码库主语言已切换为 Rust（原 TypeScript CLI 进入维护模式），整体活跃度极高。

---

## 架构

**顶层划分：两个子目录**

- `codex-rs/`：主体，一个 Cargo workspace，含约 60+ 个 crate，是所有核心逻辑所在
- `codex-cli/`：遗留 TypeScript 包装层，`bin/codex.js` 只是个分发用的 shim，实际调用的是 Rust 二进制

**`codex-rs/` 内的关键 crate**

- **`core`（`codex-core`）**：业务逻辑核心。session 管理、turn 执行、prompt 组装、context compaction、多 agent 控制（mailbox 模式）、context 装配（来自 execpolicy、sandbox、MCP、skills 等）。注意：这个 crate 已经太臃肿，团队在 `AGENTS.md` 里明确写"resist adding code to codex-core"，新功能优先建独立 crate。

- **`tui`（`codex-tui`）**：交互式全屏终端 UI，用 [Ratatui](https://ratatui.rs/) 构建。聊天、审批弹窗、协作模式切换、会话历史、技能面板都在这里。`app.rs` 是主入口（高度中心化，团队也在限制继续往里堆）。

- **`exec`（`codex-exec`）**：无头模式（headless）CLI，对应 `codex exec PROMPT`。prompt 进去、任务跑完、结果打到 stdout、自动退出。适合 CI 或脚本调用。

- **`exec-server`**：独立的执行服务器进程（较新，从 `exec` 分离），把沙盒内的文件系统和进程操作抽成独立服务。

- **`cli`（`codex-cli` multitool）**：统一入口，所有子命令（`codex`、`codex exec`、`codex mcp-server`、`codex sandbox`、`codex mcp` 等）都从这里分发。

- **`app-server` + `app-server-protocol`**：JSON-RPC v2 服务器。TUI、VS Code/Cursor/Windsurf 扩展都是通过这个 server 和 core 通信的。v1 API 已冻结，v2 是活跃开发面。`device_key_api.rs` 是最近新增的远程控制认证接口。

- **`execpolicy`**：命令审批规则引擎。解析 `policy` 文件里的 prefix/network 规则，对 agent 提出的 shell 命令逐条打 `Allow/Deny/Prompt` 决策。不是沙盒本身——它是"哪些命令需要用户确认"的决策层。

- **`sandboxing` + `linux-sandbox`**：跨平台沙盒抽象层和 Linux 实现（bubblewrap + Landlock）。macOS 用 Seatbelt（`/usr/bin/sandbox-exec`），Windows 有独立的 `windows-sandbox-rs`。

- **`device-key`**：硬件密钥基础设施，用于 remote control（把 CLI 和 chatgpt.com 打通）。密钥分三类：macOS Secure Enclave（`dk_hse_`）、Windows TPM2（`dk_tpm_`）、OS 软件保护兜底（`dk_osn_`）。当前 Linux 默认是 `UnsupportedDeviceKeyProvider`——硬件支持未完成。

- **`rollout`**：Session 持久化。会话文件写到 `~/.codex/sessions/`，可按线程 ID 恢复，支持归档（`archived_sessions/`）。

- **`codex-mcp` + `mcp-server`**：MCP 双向支持——作为 client 连接外部 MCP server，或以 `codex mcp-server` 身份被其他 agent 调用。

- **`protocol`**：跨 crate 共享的协议类型（session、turn、config_types、approvals 等）。

- **`model-provider`**：模型后端适配层。支持 OpenAI Responses API（默认）、Realtime WebRTC（语音/实时）、LMStudio（本地模型）、Ollama（本地模型）、代理转发（`responses-api-proxy`）。

- **`skills`**：技能/slash 命令系统，内置 `awaiter`（等待任务完成的子 agent）和 `explorer` 等内置角色。

**数据流主路径**

用户输入（TUI 键盘 / `codex exec` 参数）→ `cli` 分发 → `tui` 或 `exec` 触发 → `app-server` JSON-RPC（TUI 场景）或直接调 `core` → `core` 组装 prompt + 调用 `model-provider` → Responses API 返回 stream → `core` 解析工具调用 → `execpolicy` 决策 → 沙盒内执行 → 输出返回 `core` → `tui` 渲染

---

## 概念词典

- **Rollout**：一次 Codex 会话的磁盘持久化文件，存在 `~/.codex/sessions/<thread-id>/`。包含完整的 turn 历史，下次启动可以 resume。`codex exec --ephemeral` 跑完不写盘。

- **Collaboration mode**：模型 + 行为参数的预设组合（如 Default、Plan mode），在 TUI 里可切换。Plan mode 下默认 `medium` reasoning effort，设了 `plan_mode_reasoning_effort` 可以覆盖。

- **execpolicy**：命令审批规则引擎，不是沙盒。它读 policy 文件，对 agent 要执行的 shell 命令判 `Allow / Deny / Prompt（等用户确认）`。沙盒负责隔离执行环境，execpolicy 负责决定哪些命令根本不用问用户。

- **sandbox_mode**：沙盒宽松度的全局开关。`read-only`（默认，只读文件系统）、`workspace-write`（允许在当前 workspace 目录写入，同时锁网络）、`danger-full-access`（关掉沙盒，仅用于已在容器里的场景）。

- **Seatbelt**：macOS 上的沙盒机制，内核级 sandbox profile（`/usr/bin/sandbox-exec`）。Codex 在 macOS 的沙盒基于它。测试里 `CODEX_SANDBOX=seatbelt` 是检测自己是否跑在 Seatbelt 下的标志变量。

- **bubblewrap（bwrap）**：Linux 上的轻量沙盒工具，用 namespace + seccomp 做进程隔离。Codex 的 Linux 沙盒基于它，搭配 Landlock 做文件系统访问控制。

- **Landlock**：Linux 5.13+ 的内核级文件系统访问控制机制。Codex Linux 沙盒里用它精确限制哪些目录可读/可写，比 seccomp 规则更细粒度。

- **Device key**：绑定到本机硬件的 ECDSA P-256 密钥对，用于向 chatgpt.com 的 remote control 端点做身份证明。key ID 带前缀：`dk_hse_`（macOS Secure Enclave）、`dk_tpm_`（Windows TPM2）、`dk_osn_`（OS 软件保护兜底）。私钥不可导出，由 OS 硬件托管。

- **DeviceKeyProtectionPolicy**：创建 device key 时的保护策略枚举。`HardwareOnly` 只接受真正的硬件安全存储；`AllowOsProtectedNonextractable` 在没有 TPM/Secure Enclave 时允许 fallback 到软件保护密钥。

- **Remote control**：让 chatgpt.com 网页端向本机 CLI 发指令的功能。Device key 是这个功能的安全基础——用 challenge-response 证明 CLI 进程是绑定到已授权设备的，而不是中间人。

- **app-server（app-server v2）**：运行在本地的 JSON-RPC over WebSocket 服务器，充当 TUI 和 IDE 扩展的协议代理。v1 API 已冻结不再新增，v2 是所有新 API 面的开发位置。

- **Responses API**：OpenAI 的 server-sent events 式推理接口（区别于 Chat Completions API），Codex 的默认模型通信通道。WebSocket prewarm 是 v2 专有优化：在 turn 开始前预热 WebSocket 连接，降低首 token 延迟。

- **Context compaction（Compact）**：上下文压缩机制。当 session 历史过长逼近模型 context window 时，自动把历史 turn 压成摘要，释放 token 空间继续工作，不用重开会话。

- **MCP（Model Context Protocol）**：OpenAI/Anthropic 等推的工具调用协议标准。Codex 既能作为 MCP client（连接外部 MCP server 扩展工具），也能以 `codex mcp-server` 身份对外提供能力给其他 agent 调用。

- **Skills / slash commands**：可复用的 agent 能力包，用 TOML 文件定义（如内置的 `awaiter.toml`）。用户在 TUI 里用 `/` 前缀触发，也可以在 `.codex/skills/` 里自定义。

- **`codex exec`**：非交互式执行模式，`codex exec PROMPT` 或 `echo "..." | codex exec "..."` 。跑完就退出，输出到 stdout，适合 CI 管道。`--ephemeral` 跑完不写 rollout 文件。

- **`codex-core` bloat**：已知架构债务——`core` crate 因为历史原因积累了太多功能，团队在 `AGENTS.md` 里明确要求新功能"resist adding to codex-core"，优先开新 crate。

- **`x-codex-turn-state`**：HTTP header，用于 Responses API 的 sticky routing（确保同一 turn 内的多次请求落到同一后端节点），存在 `ModelClientSession` 里。

- **`CODEX_SANDBOX_NETWORK_DISABLED`**：环境变量，沙盒启动时由 Codex 自动设置为 `1`，测试代码用它判断网络是否被沙盒切断。不要在自定义代码里改它。

---

## 读者画像

核心用户是独立开发者和 AI 工程师，日常场景：在终端里给 Codex 一个任务描述，让它自主读代码、写补丁、跑测试、提 commit——自己去做别的事，回来看结果，对有风险的命令再逐条确认。 

也适合有安全意识的团队把 `codex exec` 嵌进 CI 管道，或者用 MCP server 模式让 Codex 成为更大 agent 系统里的一个工具节点。

不太适合：只想要代码补全（用 GitHub Copilot/Cursor 更合适）；或者要求零延迟响应的场景（Codex 每个 turn 要调远程 API）。

---

## 跟踪焦点

**最近 30–60 天的主线**

看分支密度，可以判断出三条同时在推的主线：

一是 **Windows sandbox 系列**——`windows-sandbox-*` 分支有超过 10 条，涉及 USERPROFILE ACL 继承修复（`windows-sandbox-bugs-15277-recursive-acl-repair`）、profile bootstrap 修复、Remoteports 豁免、WindowsApps 豁免等。Windows 沙盒是明显的高优修缮区。

二是 **Realtime WebRTC 系列**——`realtime-webrtc-*`、`realtime-v2-*`、`realtime-transport-config` 等分支集中投入，语音输入和实时流媒体传输是正在冲的能力，TUI 里已有 `voice.rs` 和 `audio_device.rs`。

三是 **subagent / goal-mode / exec-server 系列**——`subagent-mcp-mode-mvp`、`subagent-parent-mailbox`、`goal-mode-1~5`（分 5 个 PR 在 app-server、tools、core runtime、TUI 层依次落地）、`exec-server-*`——多 agent 编排和独立执行服务器是明显的产品方向。

**当前正在冲的事**

- `device-key` 的平台完整支持：当前 Linux 默认提供方是 `UnsupportedDeviceKeyProvider`（直接报错），macOS/Windows 的硬件实现还不在此次克隆的代码里——可能是平台特性门控，等待补全。
- Conversational permissions（`conv-perms-pr1~pr6`）：把权限请求/审批做成更自然的对话流程，6 条 PR 按层次依次推。
- Sticky thread environments（`sticky-thread-environments-20260420`、`turn-environments-20260417`）：让每个 thread 的环境变量在多个 turn 间保持一致。

**已知结构性痛点**

- `codex-core` 过大——团队自己在 `AGENTS.md` 里写明这是已知问题，新功能要另开 crate，但惯性下仍容易往里堆。
- App-server v1/v2 双轨——v1 已冻结但仍存在，IDE 扩展可能还依赖 v1 的部分接口，新人容易往错误的地方加 API。
- `codex-rs/tui/src/app.rs` 和 `chatwidget.rs` 是高频变更热点，`AGENTS.md` 明确限制继续往这两个文件里添加独立方法，但它们仍然是 TUI 逻辑的主入口，改动容易踩到快照测试（`insta`）。
