# GitHub Daily · 辛哥的开源风向

每天自动产出一份面向 developer 的技术早报，覆盖两类信号：

1. **AI Agent 标杆项目动态** — 深度跟踪 4 个仓库（claude-code / codex / openclaw / hermes-agent）的 issues、PRs、commits、releases
2. **GitHub 开源社区风向** — Daily Trending Top 1 + Weekly Trending Top 10，附代码级点评

产物：HTML 网页 + A4 PDF + 移动端 JPEG（适配微信/飞书分享）

## 核心原则

- **Agent-independent**：采集、分析、出刊、发布四层解耦，不绑定任何 agent 运行时
- **Source-first**：直接从 GitHub 页面与 `gh` CLI 获取原始信号
- **Publishable artifact**：项目本身产出刊物；下游 agent 只负责取刊和分发

## 环境要求

- Python 3.11+
- [`gh` CLI](https://cli.github.com/) 已安装并完成 `gh auth login`
- [`claude` CLI](https://claude.ai/code) 已登录（默认路径 `~/.local/bin/claude`，可在 `config.yaml` 里覆盖）

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install chromium   # 一次性，约 150 MB
```

## 使用

**完整 pipeline（每日运行）：**
```bash
python -m run
python -m run --only render             # 只重新渲染
python -m run --skip render             # 跳过渲染
python -m run --workers 8 --verbose
```

**单独运行某一阶段：**
```bash
python -m collectors.trending           # 抓 Trending → data/trending/YYYY-MM-DD.json
python -m collectors.repos              # 抓跟踪仓库 → data/github-daily.db
python -m analysts.narrator             # 生成 repo 叙事（sonnet）
python -m analysts.trending_reviewer    # 点评 trending 仓库代码（opus，有缓存）
python -m analysts.editor               # 汇总 → data/publications/YYYY-MM-DD.md
python -m renderers.publication         # 渲染 HTML / PDF / JPEG
```

**Web UI：**
```bash
python -m web                           # 默认 http://127.0.0.1:8765
python -m web --host 0.0.0.0 --port 8765
```

**定时任务（macOS launchd）：**
```bash
python -m schedules.install install     # 安装，每天 config.yaml 里配置的时间自动跑
python -m schedules.install uninstall
python -m schedules.install status
```

## 反向代理（可选）

Web UI 支持通过 `--root-path` 部署在子路径下，适合在 nginx 反代后使用：

```bash
python -m web --host 127.0.0.1 --port 8766 --root-path /github-daily
```

对应的 nginx location 块：

```nginx
location = /github-daily {
    return 301 /github-daily/;
}

location /github-daily/ {
    proxy_pass http://127.0.0.1:8766/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 86400;
}
```

## 目录结构

```
collectors/     原始数据采集（trending + repos）
analysts/       AI 写作角色（narrator / trending_reviewer / editor）
renderers/      出刊渲染（publication.py → HTML / PDF / JPEG）
web/            FastAPI 浏览 UI
schedules/      launchd plist 生成器
prompts/        系统提示词（SOUL.md 定义刊物风格）
assets/themes/  CSS + JS（apple.css / mobile.css / shell.html）
data/           运行时产物（不进 git）
config.yaml     运行时配置
```

## 配置

`config.yaml` 控制跟踪仓库列表、采集上限、出刊时间、模型选择。初次运行时若不存在会自动创建默认配置。
