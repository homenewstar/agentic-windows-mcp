<div align="center">

<img src="assets/logo.png" alt="Agentic Windows MCP" width="120">

# Agentic Windows MCP

### 无论你身在地球何处，都能让 AI 智能体亲手操作你的 Windows 电脑

**完整的远程桌面控制 · 深度的浏览器 DevTools · 由任意 AI 智能体在你自己的机器上真刀真枪地写代码**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE.md)
[![PyPI](https://img.shields.io/pypi/v/agentic-windows-mcp.svg)](https://pypi.org/project/agentic-windows-mcp/)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-blue)
![Python](https://img.shields.io/badge/Python-3.12%2B-yellow)
![Protocol](https://img.shields.io/badge/Protocol-MCP-purple)
![Tools](https://img.shields.io/badge/Tools-40-orange)
[![Trendshift](https://img.shields.io/badge/Trendshift-跟踪趋势-ff6b35)](https://trendshift.io)

[English](README.md) · [Tiếng Việt](README.vi.md) · [한국어](README.ko.md) · **中文**

</div>

---

## 🎬 眼见为实

左边是 **muse.ai**(Meta 托管的智能体)。右边是**从零开始打造的它的自托管克隆版**——而驱动这两块屏幕的双手，属于通过 Agentic Windows MCP 连接的 AI 智能体。视频中的每一次点击、每一次按键、每一次文件修改和浏览器检查，都由智能体在一台真实的 PC 上、通过本服务器完成。

<div align="center">

<video src="https://github.com/homenewstar/agentic-windows-mcp/raw/refs/heads/main/assets/demo-clone.mp4" controls width="100%"></video>

<a href="assets/demo-clone.mp4"><img src="assets/demo-poster.jpg" alt="观看：muse.ai 原版（左）与通过 Agentic Windows MCP 由智能体构建的，位于右侧" width="100%"></a>

▶ **[观看演示视频 — 34 秒](assets/demo-clone.mp4)**：原版与克隆版并排对比，两者都由 AI 智能体实时操作。

</div>

---

## 🤖 一款 100% 自动诞生的产品——没有人类的手

这个项目本身就是最好的证明。它由一个**全自动三人组**完整打造，端到端地取代了人类开发者：

| 三人组 | 在流水线中的角色 |
|---|---|
| **ChatGPT Web** | 大脑：规划、编码、审查、决策——像人一样通过浏览器工作 |
| **GitHub** | 脊梁：源码、版本历史、发布、CI |
| **一台个人电脑** | 工作场所：代码真正运行的地方、应用真正打开的地方、测试真正执行的地方 |

ChatGPT Web 连接到 GitHub 和这台个人电脑，而"*写代码 → 在真机上运行 → 看真实屏幕 → 修复 → 提交*"的循环**全自动运转，取代了人类**。

这个循环最具代表性的成果就是上面的演示视频：一个从零克隆的 muse.ai，并与原版逐屏进行了对照验证：

- 🎯 **前端还原度：99%**——布局、间距、排版与交互与参照原版逐像素吻合
- ⚙️ **后端覆盖率：> 60%**——重新实现了智能体循环、工具、记忆、调度、审批与安全模型
- ⏱️ **总耗时：1 天多一点**——从一个空仓库到可用的自托管产品

Agentic Windows MCP 正是让这"三人组"中的"个人电脑"一环成为现实的组件。**装上它，你用的任何智能体都能在你的机器上获得同样的能力。**

---

## 💡 核心理念——三大支柱

单靠 AI 智能体，它只能"说"。单靠你的 PC，它只是"坐着"。**Agentic Windows MCP** 是把这两者——再加上 GitHub——连成一台完整工作机器的桥梁：

| 支柱 | 角色 | 所在位置 |
|---|---|---|
| **1. GitHub** | 源码枢纽：同步、版本历史、一切变更的汇合点 | GitHub 云端 |
| **2. AI 智能体** | "大脑"：Muse、ChatGPT、Claude、Gemini、DeepSeek……分析、决策、写代码、下达命令 | 网页、手机 App、另一台机器——任何地方 |
| **3. 你的电脑 / 服务器** | "双手 + 真实的工作场所"：项目在这里、代码在这里运行、真实应用在这里打开——Agentic Windows MCP 就安装在这里负责执行 | 家用 PC、办公电脑、Windows VPS…… |

这三大支柱在**地理上完全独立**，仅靠两条管道相连：**MCP 协议**(智能体 ⇄ 你的机器)和 **Git**(你的机器 ⇄ GitHub)。因此：

- 你坐在咖啡馆里，打开手机上的 ChatGPT → 下达指令 → 智能体操控家里的 PC：打开项目、修改代码、运行测试、提交并推送到 GitHub。你回到家时，一切都已完成。
- 或者把三者合并到**一台机器**上：智能体应用就跑在这台 PC 上、代码也在这台 PC 上——Agentic Windows MCP 让智能体像坐在机器前的程序员一样操作它。

---

## ✨ 核心功能

### 🖥️ 全面的桌面控制
- **像人一样的鼠标与键盘**：单击、双击/右键、多点拖拽路径、按住不放、滑动、Unicode 文本输入、快捷键组合、滚动——精确到像素。
- **通过 UI 自动化树读取屏幕**：活动窗口全部元素的"快照"(按钮、输入框、标签……)并附带编号截图标注——智能体**无需视觉模型**就能理解界面。
- **窗口与应用管理**：启动应用、切换/关闭/调整窗口、多显示器、虚拟桌面、区域截图。
- **剪贴板、通知、显示**：读写剪贴板、发送系统通知、管理屏幕。

### ⚙️ 系统自动化
- **不受限的 PowerShell**：执行命令、安装软件、管理整个系统。
- **FileSystem**：读取/写入/列出/移动文件与文件夹——直接阅读和修改源码。
- **Registry 与 Process**：查看/修改注册表、列出与终止进程。
- **Scrape**：下载并提取网页内容。

### 🌐 像真正的 DevTools(F12)一样检查浏览器
浏览器工具集通过 **CDP(Chrome DevTools Protocol)** 连接到你已经打开的 Chrome/Edge/Brave：
- 导航、标签页管理、DOM 快照、元素查询、可视化高亮、执行任意 JavaScript。
- 深度 CSS：计算样式、**匹配规则**(哪条规则胜出、来自哪个样式表)、`::before`/`::after`、动画——以及**无障碍树**。
- **深度网络**：响应体、请求负载、各阶段计时(DNS/TCP/TLS/TTFB)、传输大小、重定向链、失败请求、WebSocket 帧、TLS 证书详情。
- **应用存储**：Cookie(包括 **HttpOnly**)、local/session 存储、IndexedDB、Cache Storage、Service Worker。
- **源文件**：列出并读取每一个脚本/样式表的完整源码。

### 👨‍💻 在你自己的机器上远程编程
组合这些工具，智能体可以在你的 PC 上完成一项编程任务的完整生命周期：打开 IDE/项目文件夹 → 阅读代码 → 修改文件 → 通过 PowerShell 构建与测试 → 打开浏览器用 DevTools 验证结果 → 修复 → `git commit` 并 `git push` 到 GitHub。你只需要下达命令并监督——用手机也可以。

### 🛡️ 安全与控制权始终归你
- **始终开启的视觉指示**：当 AI 正在控制时，你的屏幕会显示**发光边框** + 顶部边缘的提示面板 + 光标周围的光晕——它永远不会"偷偷"控制你的电脑。
- **夺回控制权快捷键**：按下 **`Ctrl + Alt + Shift + Space`** 立即夺回控制权；智能体只有在你空闲足够久之后才会恢复。
- **三级控制策略**(`relaxed` / `balanced` / `strict`)：在 strict 下，*每一个*动作都要经过控制网关并显示指示。
- **智能体优先模式**(可选)：智能体不会被普通的鼠标/键盘输入打断——只有上面的快捷键组合能接管。
- **强制身份验证**：通过 `Authorization: Bearer <token>` 请求头的 API 密钥或 OAuth；只应通过私有网络(Tailscale/WireGuard)或 HTTPS 暴露端点。
- **默认无遥测**：源码中不带任何跟踪密钥；只有当你自己设置了 `POSTHOG_API_KEY` 环境变量时，使用分析才会启用。

---

## 🏗️ 工作流与架构

<div align="center">

<img src="assets/workflow.png" alt="工作流：一次工具调用的完整流程" width="100%">

*一次工具调用的完整流程——请求 → 规划 → 连接 → 身份验证 → 控制网关 → 执行 → 结果。[交互版](assets/workflow.html)。*

<img src="assets/architecture.png" alt="运行时架构" width="100%">

*运行时架构——传输 + 身份验证、FastMCP 服务器、控制面，以及 Windows 之上的桌面 / 系统 / 浏览器三层。[交互版](assets/architecture.html)。*

</div>

```
                    ┌─────────────────────────────┐
                    │           GITHUB            │
                    │   source · sync · releases  │
                    └──────────────┬──────────────┘
                          git push/pull
                    ┌──────────────▼──────────────┐
   ┌─────────────┐  │   YOUR COMPUTER             │      ┌──────────────────┐
   │  AI AGENT   │  │   ┌───────────────────────┐  │      │      USER        │
   │ Muse ·     ├──┼──►│  AGENTIC WINDOWS MCP  │◄─┼──────┤ supervises ·     │
   │ ChatGPT ·   │MCP│   │  40 control tools     │  │ sees │ glowing border   │
   │ Claude ·    │◄──┼──┤  mouse · keys · apps  │  │      │ · notice panel   │
   │ Gemini ·    │  │   │  PowerShell · files   │  │      │ · Space chord*   │
   │ DeepSeek... │  │   │  Browser DevTools     │  │      └──────────────────┘
   └─────────────┘  │   └───────────────────────┘  │
   (web / app /     │   Windows 10 / 11            │
    another PC)     └─────────────────────────────┘
                     *take-back chord: Ctrl + Alt + Shift + Space
```

```
Agent (MCP client)
   │  JSON-RPC: stdio / Streamable HTTP (+ Bearer token / OAuth)
   ▼
Agentic Windows MCP (FastMCP, Python)
   ├── Desktop layer .... Win32 API + UI Automation (element tree, screenshots)
   ├── Control plane .... the gate: glowing border, notice panel, policies
   │                      relaxed/balanced/strict, take-back chord, lease queue
   ├── System layer ..... PowerShell, FileSystem, Registry, Process
   └── Browser layer .... Playwright + raw CDP (Network/CSS/DOM/
                          Accessibility/Debugger/Storage domains)
   ▼
Windows 10 / 11
```

主要源码位于 `src/agentic_windows_mcp/`(`desktop/`、`browser/`、`tools/`、`infrastructure/`、`filesystem/`、`powershell/`、`registry/`、`uia/`……)。

---

## 🚀 安装(在将被控制的 Windows 机器上)

**要求**：Windows 10/11 · Python 3.12+ · [uv](https://docs.astral.sh/uv/) · Git。

### 方式一——从 PyPI 安装(最快)

```powershell
pip install agentic-windows-mcp
# 或者用 uv 隔离安装：
uv tool install agentic-windows-mcp
```

### 方式二——从源码安装

```powershell
git clone https://github.com/homenewstar/agentic-windows-mcp.git
cd agentic-windows-mcp
uv sync
```

> 浏览器/DevTools 工具通过 CDP 与你现有的 Chrome/Edge 通信。如果你还希望服务器能自行启动 Chromium，请运行一次 `playwright install chromium`。

### 运行服务器

**HTTP 模式(供远程智能体 / 通过网络使用)**——带身份验证密钥：

```powershell
# 创建 / 更换验证密钥(保存在 ~/.agentic-windows-mcp/)
agentic-windows-mcp auth

# 通过 HTTP 启动 MCP 服务器
agentic-windows-mcp serve --transport streamable-http --host 0.0.0.0 --port 8000
# 端点：http://<你的PC的IP>:8000/mcp
```

(如果从源码安装，请在命令前加上 `uv run` 前缀。)

**stdio 模式(智能体在同一台机器上**，例如 Claude Desktop、Cursor、VS Code)：见下方连接配置——客户端会通过 `uvx` 自行启动服务器。

### 随 Windows 自动启动

```powershell
agentic-windows-mcp install     # 创建 "agentic-windows-mcp-server" 计划任务
agentic-windows-mcp uninstall   # 删除任务并停止服务器
```

### 安全地暴露端点

切勿把端口直接开放在互联网上。请使用以下方式之一：

- **Tailscale**(推荐)：在 PC 和你的设备上都安装 Tailscale，然后执行 `tailscale serve --bg --https=443 http://127.0.0.1:8000`——端点只会出现在你的私有 tailnet 中。
- **WireGuard / 自建 VPN**，或带身份验证的 HTTPS 反向代理(Caddy/Nginx)。

### 配置

配置文件位于 `~/.agentic-windows-mcp/config.toml`：

```toml
[server]
# 默认 host / port / transport...

[control]
enabled = true
policy = "strict"        # relaxed | balanced | strict
idle_timeout_seconds = 10
```

---

## 🔌 连接你的 AI 智能体

通用原则：**任何支持 MCP(Model Context Protocol)的应用都可以连接**——通过 stdio(同一台机器)或 Streamable HTTP(通过网络，并带 `Authorization: Bearer <API_KEY>` 请求头)。

### Muse(muse.ai)与网页/App 智能体
基于网页的智能体不会在你的机器上安装软件——它们连接到 Agentic Windows MCP 的 HTTP 端点：

1. 按上文以 HTTP 模式运行服务器，并通过 Tailscale/HTTPS 暴露。
2. 在智能体中添加一个**自定义连接器 / MCP 服务器**，URL 为 `https://<你的机器>/mcp`，并填入你的密钥。
3. 智能体将看到全部 40 个工具，并能操控你的 PC——包括打开项目、修改代码并推送到 GitHub。

### ChatGPT
ChatGPT 通过**开发者模式 / 连接器**支持 MCP：

1. 设置 → 连接器 → 启用开发者模式 → *创建*一个新的连接器。
2. 输入 MCP URL(`https://<你的机器>/mcp`)和 API 密钥。
3. 在对话中启用该连接器并下达指令，例如：*"在我的电脑上打开项目 X，运行测试并修复所有失败项。"*

### Claude(Claude Desktop 与 Claude Code)
同一台机器——stdio 模式，Claude 会自行启动服务器：

```json
{
  "mcpServers": {
    "agentic-windows-mcp": {
      "command": "uvx",
      "args": ["agentic-windows-mcp"]
    }
  }
}
```

不同的机器——指向 HTTP 端点：

```json
{
  "mcpServers": {
    "agentic-windows-mcp": {
      "url": "http://<你的PC的IP>:8000/mcp",
      "headers": { "Authorization": "Bearer <API_KEY>" }
    }
  }
}
```

Claude Code：`claude mcp add --transport http agentic-windows-mcp http://<你的PC的IP>:8000/mcp --header "Authorization: Bearer <API_KEY>"`。

### Gemini
- **Gemini CLI**：把与上面 Claude 示例相同的 `mcpServers` 配置块添加到 `~/.gemini/settings.json`(stdio，或 `httpUrl` + `headers`)。
- **Google AI Studio / Gemini API**：使用你的应用内置的 MCP 客户端，指向同一个 HTTP 端点。

### DeepSeek 与其他所有智能体
DeepSeek(以及其他模型)有两种连接方式：

- **通过现有的 MCP 宿主**：Cursor、VS Code(Copilot 智能体模式)、Cline、Continue、Windsurf……——只需添加上面的 `mcpServers` 配置块；背后用的是什么模型并不重要，DeepSeek 也可以。
- **直接通过 HTTP**：任何能向 `/mcp` 以 JSON-RPC POST 并带上验证请求头的智能体框架(LangChain、LlamaIndex、你自己的代码……)都能工作。

### 速查表

| 智能体 / 客户端 | 连接方式 | 备注 |
|---|---|---|
| Muse(网页/App) | HTTP 连接器 | 远程智能体，通过 Tailscale/HTTPS 操控 PC |
| ChatGPT | HTTP 连接器(开发者模式) | 必须启用开发者模式 |
| Claude Desktop / Code | stdio 或 HTTP | 同一台机器时用 stdio |
| Gemini CLI | stdio 或 HTTP | `mcpServers` 配置 |
| DeepSeek | 通过 MCP 宿主(Cursor/Cline/VS Code……) | 模型可自由替换 |
| Cursor / VS Code / Windsurf | stdio 或 HTTP | 添加 `mcpServers` 配置块 |

---

## 🧰 工具集(40 个工具)

| 分组 | 工具 |
|---|---|
| **桌面控制** | `Click`、`Type`、`Move`、`Scroll`、`Shortcut`、`MultiSelect`、`MultiEdit`、`Snapshot`、`Screenshot`、`Wait`、`WaitFor` |
| **应用与系统** | `App`、`PowerShell`、`FileSystem`、`Registry`、`Process`、`Clipboard`、`Notification`、`DisplayInventory`、`Scrape` |
| **控制网关** | `ControlStatus`(当前谁在掌控、正在应用哪种策略) |
| **浏览器(CDP)** | `Browser_Connect`、`Browser_Navigate`、`Browser_ManageTabs`、`Browser_GetPageSnapshot`、`Browser_QueryElements`、`Browser_Interact`、`Browser_TakeScreenshot`、`Browser_Highlight`、`Browser_EvaluateScript` |
| **深度 DevTools** | `Browser_GetCssStyles`、`Browser_GetComputedStyle`、`Browser_GetMatchedStyles`、`Browser_GetAccessibilityTree`、`Browser_ListConsoleMessages`、`Browser_ListNetworkRequests`、`Browser_GetNetworkDetails`、`Browser_GetStorage`、`Browser_ListPageSources`、`Browser_GetSourceText` |

---

## 💬 给智能体的指令示例

- *"在我的电脑上打开项目 `D:\code\shop-web`，运行测试，修复所有失败项，然后提交并推送到 GitHub。"*
- *"我的主页加载很慢——在 Chrome 中打开它，检查网络标签页，告诉我哪些请求慢、为什么慢。"*
- *"读取 Downloads 文件夹里的销售报表电子表格，按月汇总营收并另存为一个新文件。"*
- *"在这台机器上安装 Node.js 和 VS Code，并用我的姓名和邮箱配置 git。"*
- *"打开笔记应用，把剪贴板里今天的会议内容粘贴进去并保存。"*

---

## 📈 趋势跟踪

在 **[Trendshift](https://trendshift.io)** 上关注本项目——那是记录 GitHub 仓库趋势历史的平台：

<div align="center">

[![Trendshift](https://img.shields.io/badge/Trendshift-探索正在流行的仓库-ff6b35?style=for-the-badge)](https://trendshift.io)

</div>

> 一旦本仓库登上 GitHub Trending，这里的位置将嵌入其实时 Trendshift 徽章(排名与历史)。

---

## 🤝 参与贡献

欢迎贡献——请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。简而言之：所有工作都在 `main` 分支上进行，且变更合入前测试套件必须全部通过(`uv run pytest`)。

## 🔒 安全

此服务器能够在其运行的机器上执行真实且不可逆的操作。部署前请阅读 [SECURITY.md](SECURITY.md)，并通过 [GitHub Security Advisories](https://github.com/homenewstar/agentic-windows-mcp/security/advisories/new) 私下报告漏洞——切勿公开提交 issue。

## 📄 许可证

基于 **Apache License 2.0** 发布——© 2026 **Vu Manh Ruy**。详情见 [LICENSE.md](LICENSE.md) 与 [NOTICE](NOTICE)。
