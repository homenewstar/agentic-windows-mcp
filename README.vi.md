<div align="center">

<img src="assets/logo.png" alt="Agentic Windows MCP" width="120">

# Agentic Windows MCP

### Đôi tay của AI Agent trên chiếc PC Windows của bạn — từ bất cứ nơi đâu trên Trái Đất

**Điều khiển máy tính từ xa toàn diện · DevTools trình duyệt chuyên sâu · Lập trình thật trên máy của bạn, do bất kỳ AI Agent nào điều khiển**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE.md)
[![PyPI](https://img.shields.io/pypi/v/agentic-windows-mcp.svg)](https://pypi.org/project/agentic-windows-mcp/)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-blue)
![Python](https://img.shields.io/badge/Python-3.12%2B-yellow)
![Protocol](https://img.shields.io/badge/Protocol-MCP-purple)
![Tools](https://img.shields.io/badge/Tools-40-orange)
[![Trendshift](https://img.shields.io/badge/Trendshift-Theo%20dõi%20xu%20hướng-ff6b35)](https://trendshift.io)

[English](README.md) · **Tiếng Việt** · [한국어](README.ko.md) · [中文](README.zh.md)

</div>

---

## 🎬 Xem thực tế

Nửa trái là **muse.ai** (agent chạy trên nền tảng của Meta). Nửa phải là **bản clone tự host của chính nó, được dựng từ con số 0** — và đôi tay đang điều khiển cả hai màn hình thuộc về các AI Agent kết nối qua Agentic Windows MCP. Mọi cú click, phím bấm, sửa file và soi trình duyệt trong video này đều do agent thực hiện, trên một chiếc PC thật, thông qua server này.

<div align="center">

<video src="https://github.com/homenewstar/agentic-windows-mcp/raw/refs/heads/main/assets/demo-clone.mp4" controls width="100%"></video>

<a href="assets/demo-clone.mp4"><img src="assets/demo-poster.jpg" alt="Xem: bản gốc muse.ai (trái) và bản clone tự host do agent dựng qua Agentic Windows MCP (phải)" width="100%"></a>

▶ **[Xem video demo — 34 giây](assets/demo-clone.mp4)**: bản gốc vs. bản clone, đặt cạnh nhau, cả hai đều được AI Agent vận hành trực tiếp.

</div>

---

## 🤖 Một sản phẩm được tạo ra 100% tự động — không bàn tay con người

Chính dự án này là minh chứng sống. Nó được tạo ra **hoàn toàn bởi một bộ ba tự động**, thay thế trọn vẹn vai trò của lập trình viên:

| Bộ ba | Vai trò trong dây chuyền |
|---|---|
| **ChatGPT Web** | Bộ não: lập kế hoạch, viết code, rà soát, ra quyết định — qua trình duyệt, như một con người |
| **GitHub** | Xương sống: kho mã nguồn, lịch sử phiên bản, phát hành, CI |
| **Máy tính cá nhân** | Nơi làm việc: code thực sự chạy ở đây, ứng dụng thực sự mở ở đây, test thực sự chạy ở đây |

ChatGPT Web kết nối với GitHub và với máy tính cá nhân, và vòng lặp — *viết code → chạy trên máy thật → nhìn màn hình thật → sửa → commit* — vận hành **hoàn toàn tự động, thay cho con người**.

Thành quả tiêu biểu nhất của vòng lặp đó là video demo phía trên: một bản clone dựng từ đầu của muse.ai, được dựng và kiểm chứng từng màn hình so với bản gốc:

- 🎯 **Độ chính xác frontend: 99%** — bố cục, khoảng cách, typography và tương tác khớp từng pixel với bản tham chiếu
- ⚙️ **Độ bao phủ backend: > 60%** — vòng lặp agent, công cụ, bộ nhớ, lịch trình, phê duyệt và mô hình bảo mật được làm lại
- ⏱️ **Tổng thời gian: hơn 1 ngày** — từ repo trống đến một sản phẩm tự host vận hành được

Agentic Windows MCP chính là thành phần biến chân "máy tính cá nhân" của bộ ba đó thành hiện thực. **Cài nó vào, và bất kỳ agent nào bạn dùng cũng có được sức mạnh tương tự trên máy của bạn.**

---

## 💡 Ý tưởng cốt lõi — Ba trụ cột

Một mình, AI Agent chỉ "nói" được. Một mình, chiếc PC của bạn chỉ "ngồi" đó. **Agentic Windows MCP** là cầu nối khiến cả hai — cộng với GitHub — tạo thành một bộ máy làm việc hoàn chỉnh:

| Trụ cột | Vai trò | Nằm ở đâu? |
|---|---|---|
| **1. GitHub** | Kho mã nguồn trung tâm: đồng bộ, lịch sử phiên bản, điểm gặp gỡ của mọi thay đổi | Trên cloud của GitHub |
| **2. AI Agent** | "Bộ não": Muse, ChatGPT, Claude, Gemini, DeepSeek... phân tích, ra quyết định, viết code, ra lệnh | Web, app điện thoại, máy khác — bất cứ đâu |
| **3. Máy tính / Server của bạn** | "Đôi tay + nơi làm việc thật": dự án nằm đây, code chạy ở đây, ứng dụng thật mở ở đây — Agentic Windows MCP được cài tại đây để thi hành | PC ở nhà, máy ở công ty, VPS Windows... |

**Ba trụ cột hoàn toàn độc lập về vị trí địa lý**, chỉ kết hợp qua hai đường ống: giao thức **MCP** (Agent ⇄ máy của bạn) và **Git** (máy của bạn ⇄ GitHub). Nhờ vậy:

- Bạn ngồi quán cà phê, mở ChatGPT trên điện thoại → ra lệnh → Agent điều khiển chiếc PC ở nhà: mở dự án, sửa code, chạy test, commit và push lên GitHub. Về đến nhà, mọi thứ đã xong.
- Hoặc gộp cả ba vào **một máy duy nhất**: Agent chạy app trên chính PC, code nằm trên chính PC — Agentic Windows MCP cho phép Agent tự thao tác như một lập trình viên ngồi trước máy.

---

## ✨ Chức năng chính

### 🖥️ Điều khiển máy tính toàn diện
- **Chuột & bàn phím như người thật**: click, double/right-click, kéo–thả theo đường nhiều đoạn, giữ chuột, vuốt/cào, gõ văn bản Unicode, tổ hợp phím tắt, cuộn — kể cả thao tác kéo chính xác từng điểm ảnh.
- **Đọc màn hình bằng cây UI (UI Automation)**: chụp "Snapshot" toàn bộ cây phần tử của cửa sổ đang mở (nút bấm, ô nhập, nhãn...) kèm ảnh chụp có đánh số — Agent hiểu giao diện **không cần mô hình thị giác**.
- **Quản lý cửa sổ & ứng dụng**: mở app, chuyển/đóng cửa sổ, thay đổi kích thước, nhiều màn hình, virtual desktop, chụp ảnh màn hình từng vùng.
- **Clipboard, thông báo, hiển thị**: đọc/ghi clipboard, gửi thông báo hệ thống, quản lý màn hình.

### ⚙️ Tự động hóa hệ thống
- **PowerShell** không giới hạn: chạy lệnh, cài đặt phần mềm, quản trị hệ thống.
- **FileSystem**: đọc/ghi/liệt kê/di chuyển file & thư mục — đọc và sửa mã nguồn trực tiếp.
- **Registry & Process**: đọc/sửa registry, liệt kê & dừng tiến trình.
- **Scrape**: tải và trích xuất nội dung trang web.

### 🌐 Kiểm tra trình duyệt như DevTools (F12) thật
Bộ công cụ Browser kết nối vào Chrome/Edge/Brave đang mở của bạn qua **CDP (Chrome DevTools Protocol)**:
- Điều hướng, quản lý tab, snapshot DOM, truy vấn phần tử, highlight trực quan, thực thi JavaScript tùy ý.
- CSS chuyên sâu: computed styles, **matched rules** (rule nào thắng, từ stylesheet nào), `::before`/`::after`, animation — và **cây Accessibility**.
- **Network chuyên sâu**: response body, request payload, timing từng pha (DNS/TCP/TLS/TTFB), kích thước truyền, redirect chain, request lỗi, frame WebSocket, chi tiết chứng chỉ TLS.
- **Application storage**: cookies (kể cả **HttpOnly**), local/session storage, IndexedDB, Cache Storage, Service Workers.
- **Sources**: liệt kê & đọc nguyên văn source của mọi script/stylesheet.

### 👨‍💻 Lập trình từ xa trên chính máy của bạn
Kết hợp các nhóm công cụ trên, Agent có thể làm trọn vòng đời của một tác vụ lập trình ngay trên PC của bạn: mở IDE/thư mục dự án → đọc code → sửa file → chạy build/test bằng PowerShell → mở trình duyệt kiểm tra kết quả bằng DevTools → sửa tiếp → `git commit` & `git push` lên GitHub. Bạn chỉ cần ra lệnh và giám sát — từ điện thoại cũng được.

### 🛡️ An toàn & quyền kiểm soát thuộc về bạn
- **Chỉ báo trực quan luôn bật**: khi AI đang điều khiển, màn hình có **viền phát sáng** + bảng báo ở cạnh trên + quầng sáng quanh con trỏ — không bao giờ điều khiển "lén".
- **Phím lấy lại quyền**: nhấn **`Ctrl + Alt + Shift + Space`** để giành lại quyền điều khiển ngay lập tức; Agent chỉ tiếp tục khi bạn đã nhàn rỗi đủ lâu.
- **Chính sách kiểm soát 3 mức** (`relaxed` / `balanced` / `strict`): ở mức strict, *mọi* thao tác đều phải qua cổng kiểm soát và hiện chỉ báo.
- **Chế độ ưu tiên Agent** (tùy chọn): Agent không bị ngắt bởi di chuột/gõ phím thông thường — chỉ phím tắt trên mới takeover được.
- **Xác thực bắt buộc**: API key qua header `Authorization: Bearer <token>` hoặc OAuth; chỉ nên phơi endpoint qua mạng riêng (Tailscale/WireGuard) hoặc HTTPS.
- **Không telemetry mặc định**: mã nguồn không chứa sẵn khóa theo dõi; phân tích sử dụng chỉ bật khi bạn tự đặt biến môi trường `POSTHOG_API_KEY`.

---

## 🏗️ Luồng xử lý & kiến trúc

<div align="center">

<img src="assets/workflow.png" alt="Luồng xử lý: một lần gọi công cụ, từ đầu đến cuối" width="100%">

*Một lần gọi công cụ, từ đầu đến cuối — yêu cầu → lập kế hoạch → kết nối → xác thực → cổng kiểm soát → thực thi → kết quả. [Bản tương tác](assets/workflow.html).*

<img src="assets/architecture.png" alt="Kiến trúc runtime" width="100%">

*Kiến trúc runtime — transport + xác thực, FastMCP server, mặt phẳng kiểm soát, và ba tầng desktop / hệ thống / trình duyệt trên Windows. [Bản tương tác](assets/architecture.html).*

</div>

```
                    ┌─────────────────────────────┐
                    │           GITHUB            │
                    │  mã nguồn · đồng bộ · bản sao │
                    └──────────────┬──────────────┘
                          git push/pull
                    ┌──────────────▼──────────────┐
   ┌─────────────┐  │   MÁY TÍNH CỦA BẠN          │      ┌──────────────────┐
   │  AI AGENT   │  │   ┌───────────────────────┐  │      │  NGƯỜI DÙNG      │
   │ Muse ·     ├──┼──►│  AGENTIC WINDOWS MCP  │◄─┼──────┤  giám sát · xem  │
   │ ChatGPT ·   │MCP│   │  40 công cụ điều khiển │  │ nhìn │  viền phát sáng  │
   │ Claude ·    │◄──┼──┤  chuột · phím · app   │  │ thấy │  · bảng cảnh báo │
   │ Gemini ·    │  │   │  PowerShell · file    │  │      │  · phím Space*   │
   │ DeepSeek... │  │   │  DevTools trình duyệt│  │      └──────────────────┘
   └─────────────┘  │   └───────────────────────┘  │
   (web / app /     │   Windows 10 / 11          │
    máy khác)       └─────────────────────────────┘
                     *phím tắt lấy lại quyền điều khiển:
                      Ctrl + Alt + Shift + Space
```

```
Agent (MCP client)
   │  JSON-RPC: stdio / Streamable HTTP (+ Bearer <redacted> / OAuth)
   ▼
Agentic Windows MCP (FastMCP, Python)
   ├── Desktop layer .... Win32 API + UI Automation (cây phần tử, ảnh chụp)
   ├── Control plane .... cổng kiểm soát: viền phát sáng, bảng báo, chính sách
   │                      relaxed/balanced/strict, phím takeover, hàng đợi lease
   ├── System layer ..... PowerShell, FileSystem, Registry, Process
   └── Browser layer .... Playwright + CDP trực tiếp (Network/CSS/DOM/
                          Accessibility/Debugger/Storage domains)
   ▼
Windows 10 / 11
```

Mã nguồn chính nằm trong `src/agentic_windows_mcp/` (`desktop/`, `browser/`, `tools/`, `infrastructure/`, `filesystem/`, `powershell/`, `registry/`, `uia/`...).

---

## 🚀 Cài đặt (trên máy Windows sẽ bị điều khiển)

**Yêu cầu**: Windows 10/11 · Python 3.12+ · [uv](https://docs.astral.sh/uv/) · Git.

### Cách 1 — từ PyPI (nhanh nhất)

```powershell
pip install agentic-windows-mcp
# hoặc, tách riêng bằng uv:
uv tool install agentic-windows-mcp
```

### Cách 2 — từ mã nguồn

```powershell
git clone https://github.com/homenewstar/agentic-windows-mcp.git
cd agentic-windows-mcp
uv sync
```

> Bộ công cụ Browser/DevTools nói chuyện với Chrome/Edge sẵn có của bạn qua CDP. Nếu muốn server tự mở Chromium riêng, chạy thêm `playwright install chromium` một lần.

### Chạy server

**Chế độ HTTP (cho Agent ở xa / qua mạng)** — kèm khóa xác thực:

```powershell
# Tạo / đổi khóa xác thực (lưu tại ~/.agentic-windows-mcp/)
agentic-windows-mcp auth

# Khởi động server MCP qua HTTP
agentic-windows-mcp serve --transport streamable-http --host 0.0.0.0 --port 8000
# Endpoint: http://<IP-máy-bạn>:8000/mcp
```

(Nếu cài từ mã nguồn, thêm tiền tố `uv run` trước các lệnh.)

**Chế độ stdio (cho Agent chạy cùng máy**, ví dụ Claude Desktop, Cursor, VS Code): xem cấu hình ở mục kết nối bên dưới — client sẽ tự khởi động server qua `uvx`.

### Tự khởi động cùng Windows

```powershell
agentic-windows-mcp install   # tạo Scheduled Task "agentic-windows-mcp-server"
agentic-windows-mcp uninstall # gỡ task và dừng server
```

### Phơi endpoint ra ngoài an toàn

Đừng mở port trực tiếp ra Internet. Hãy dùng một trong các cách:

- **Tailscale** (khuyên dùng): cài Tailscale trên cả PC và thiết bị của bạn, rồi `tailscale serve --bg --https=443 http://127.0.0.1:8000` — endpoint chỉ xuất hiện trong tailnet riêng của bạn.
- **WireGuard / VPN** tự host, hoặc reverse proxy HTTPS (Caddy/Nginx) có xác thực.

### Cấu hình

File cấu hình đặt tại `~/.agentic-windows-mcp/config.toml`:

```toml
[server]
# host / port / transport mặc định...

[control]
enabled = true
policy = "strict"        # relaxed | balanced | strict
idle_timeout_seconds = 10
```

---

## 🔌 Kết nối các AI Agent

Nguyên tắc chung: **bất kỳ ứng dụng nào hỗ trợ giao thức MCP (Model Context Protocol) đều kết nối được**, qua stdio (cùng máy) hoặc Streamable HTTP (qua mạng, kèm header `Authorization: Bearer <API_KEY>`).

### Muse (muse.ai) & các agent dạng web/app
Các agent chạy trên nền web không tự cài phần mềm lên máy bạn — chúng kết nối tới endpoint HTTP của Agentic Windows MCP:

1. Chạy server ở chế độ HTTP như trên và phơi qua Tailscale/HTTPS.
2. Trong agent, thêm **custom connector / MCP server** với URL `https://<máy-của-bạn>/mcp` và khóa xác thực.
3. Agent sẽ thấy toàn bộ 40 công cụ và bắt đầu điều khiển được PC của bạn — kể cả việc mở dự án, sửa code và đẩy lên GitHub.

### ChatGPT
ChatGPT hỗ trợ MCP qua **Developer mode / Connectors**:

1. Settings → Connectors → bật Developer mode → *Create* một connector mới.
2. Nhập URL MCP (`https://<máy-của-bạn>/mcp`) và khóa API.
3. Trong cuộc trò chuyện, bật connector và ra lệnh, ví dụ: *"Mở thư mục dự án X trên máy tôi, chạy test và sửa các lỗi đang fail."*

### Claude (Claude Desktop & Claude Code)
Cùng máy — dùng stdio, Claude tự khởi động server:

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

Khác máy — trỏ tới endpoint HTTP:

```json
{
  "mcpServers": {
    "agentic-windows-mcp": {
      "url": "http://<IP-máy-bạn>:8000/mcp",
      "headers": { "Authorization": "Bearer <API_KEY>" }
    }
  }
}
```

Claude Code: `claude mcp add --transport http agentic-windows-mcp http://<IP-máy-bạn>:8000/mcp --header "Authorization: Bearer <API_KEY>"`.

### Gemini
- **Gemini CLI**: thêm vào `~/.gemini/settings.json` đúng định dạng `mcpServers` như ví dụ của Claude ở trên (stdio hoặc `httpUrl` + `headers`).
- **Google AI Studio / Gemini API**: dùng MCP client tích hợp trong ứng dụng của bạn và trỏ tới cùng endpoint HTTP.

### DeepSeek & mọi agent khác
DeepSeek (và các mô hình khác) kết nối được theo hai cách:

- **Qua một MCP host** có sẵn: Cursor, VS Code (Copilot agent mode), Cline, Continue, Windsurf... — chỉ cần thêm khối `mcpServers` ở trên; mô hình phía sau là gì không quan trọng, kể cả DeepSeek.
- **Qua HTTP trực tiếp**: bất kỳ framework agent nào (LangChain, LlamaIndex, code tự viết...) gọi JSON-RPC tới `POST /mcp` với header xác thực đều dùng được.

### Bảng tra nhanh

| Agent / Client | Cách kết nối | Ghi chú |
|---|---|---|
| Muse (web/app) | HTTP connector | Agent ở xa, điều khiển PC qua Tailscale/HTTPS |
| ChatGPT | HTTP connector (Developer mode) | Cần bật Developer mode |
| Claude Desktop / Code | stdio hoặc HTTP | stdio khi cùng máy |
| Gemini CLI | stdio hoặc HTTP | Cấu hình `mcpServers` |
| DeepSeek | Qua MCP host (Cursor/Cline/VS Code...) | Mô hình thay thế linh hoạt |
| Cursor / VS Code / Windsurf | stdio hoặc HTTP | Thêm khối `mcpServers` |

---

## 🧰 Bộ công cụ (40 tools)

| Nhóm | Công cụ |
|---|---|
| **Điều khiển desktop** | `Click`, `Type`, `Move`, `Scroll`, `Shortcut`, `MultiSelect`, `MultiEdit`, `Snapshot`, `Screenshot`, `Wait`, `WaitFor` |
| **Ứng dụng & hệ thống** | `App`, `PowerShell`, `FileSystem`, `Registry`, `Process`, `Clipboard`, `Notification`, `DisplayInventory`, `Scrape` |
| **Cổng kiểm soát** | `ControlStatus` (xem ai đang giữ quyền điều khiển, chính sách đang áp dụng) |
| **Trình duyệt (CDP)** | `Browser_Connect`, `Browser_Navigate`, `Browser_ManageTabs`, `Browser_GetPageSnapshot`, `Browser_QueryElements`, `Browser_Interact`, `Browser_TakeScreenshot`, `Browser_Highlight`, `Browser_EvaluateScript` |
| **DevTools chuyên sâu** | `Browser_GetCssStyles`, `Browser_GetComputedStyle`, `Browser_GetMatchedStyles`, `Browser_GetAccessibilityTree`, `Browser_ListConsoleMessages`, `Browser_ListNetworkRequests`, `Browser_GetNetworkDetails`, `Browser_GetStorage`, `Browser_ListPageSources`, `Browser_GetSourceText` |

---

## 💬 Ví dụ ra lệnh cho Agent

- *"Mở dự án `D:\code\shop-web` trên máy tôi, chạy test, sửa hết lỗi đang fail rồi commit và push lên GitHub."*
- *"Trang chủ web của tôi tải chậm — mở nó trong Chrome, kiểm tra tab Network xem request nào chậm và vì sao."*
- *"Đọc file Excel báo cáo trong thư mục Downloads, tổng hợp doanh thu theo tháng và lưu thành file mới."*
- *"Cài đặt Node.js và VS Code lên máy này, cấu hình git với tên và email của tôi."*
- *"Mở ứng dụng ghi chú, chép nội dung cuộc họp hôm nay từ clipboard vào và lưu lại."*

---

## 📈 Theo dõi xu hướng

Theo dõi dự án này trên **[Trendshift](https://trendshift.io)** — nền tảng ghi lại lịch sử trending của các repo GitHub:

<div align="center">

[![Trendshift](https://img.shields.io/badge/Trendshift-Khám%20phá%20repo%20đang%20trending-ff6b35?style=for-the-badge)](https://trendshift.io)

</div>

> Ngay khi repo xuất hiện trên GitHub Trending, badge Trendshift trực tiếp (thứ hạng & lịch sử) sẽ được gắn tại đây.

---

## 🤝 Đóng góp

Mọi đóng góp đều được hoan nghênh — hãy đọc [CONTRIBUTING.md](CONTRIBUTING.md) trước. Tóm tắt: mọi thay đổi diễn ra trên nhánh `main`, và bộ test phải xanh (`uv run pytest`) trước khi một thay đổi được hợp nhất.

## 🔒 Bảo mật

Server này có thể thực hiện các hành động thật và không thể hoàn tác trên máy nó chạy. Hãy đọc [SECURITY.md](SECURITY.md) trước khi triển khai, và báo lỗ hổng một cách riêng tư qua [GitHub Security Advisories](https://github.com/homenewstar/agentic-windows-mcp/security/advisories/new) — tuyệt đối không mở issue công khai.

## 📄 Giấy phép

Phát hành theo giấy phép **Apache License 2.0** — © 2026 **Vu Manh Ruy**. Xem chi tiết trong [LICENSE.md](LICENSE.md) và [NOTICE](NOTICE).
