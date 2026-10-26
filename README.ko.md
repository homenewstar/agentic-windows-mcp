<div align="center">

<img src="assets/logo.png" alt="Agentic Windows MCP" width="120">

# Agentic Windows MCP

### 지구 어디에서든, 당신의 Windows PC를 움직이는 AI 에이전트의 손

**완전한 원격 데스크톱 제어 · 심층 브라우저 DevTools · 어떤 AI 에이전트든 당신의 PC에서 실제로 코딩**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE.md)
[![PyPI](https://img.shields.io/pypi/v/agentic-windows-mcp.svg)](https://pypi.org/project/agentic-windows-mcp/)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-blue)
![Python](https://img.shields.io/badge/Python-3.12%2B-yellow)
![Protocol](https://img.shields.io/badge/Protocol-MCP-purple)
![Tools](https://img.shields.io/badge/Tools-40-orange)
[![Trendshift](https://img.shields.io/badge/Trendshift-트렌드%20추적-ff6b35)](https://trendshift.io)

[English](README.md) · [Tiếng Việt](README.vi.md) · **한국어** · [中文](README.zh.md)

</div>

---

## 🎬 실제 동작 보기

왼쪽은 **muse.ai**(Meta의 호스팅 에이전트)입니다. 오른쪽은 **처음부터 새로 만든 그 셀프 호스팅 클론**입니다. 두 화면을 움직이는 손은 Agentic Windows MCP로 연결된 AI 에이전트들의 것입니다. 이 영상의 모든 클릭, 키 입력, 파일 수정, 브라우저 검사는 에이전트가 실제 PC에서 이 서버를 통해 수행한 것입니다.

<div align="center">

<video src="https://github.com/homenewstar/agentic-windows-mcp/raw/refs/heads/main/assets/demo-clone.mp4" controls width="100%"></video>

<a href="assets/demo-clone.mp4"><img src="assets/demo-poster.jpg" alt="보기: muse.ai 원본(왼쪽)과 Agentic Windows MCP를 통해 에이전트가 구축한 셀프 호스팅 클론(오른쪽)" width="100%"></a>

▶ **[데모 영상 보기 — 34초](assets/demo-clone.mp4)**: 원본과 클론을 나란히, 둘 다 AI 에이전트가 실시간으로 조작합니다.

</div>

---

## 🤖 사람의 손을 거치지 않고, 100% 자동으로 만들어진 제품

이 프로젝트 자체가 그 증거입니다. 이 프로젝트는 인간 개발자를 완전히 대체한 **자동화된 삼총사**에 의해 제작되었습니다.

| 삼총사 | 파이프라인에서의 역할 |
|---|---|
| **ChatGPT Web** | 두뇌: 계획, 코딩, 검토, 의사결정 — 사람처럼 브라우저를 통해 |
| **GitHub** | 척추: 소스 코드, 버전 히스토리, 릴리스, CI |
| **개인용 컴퓨터** | 작업장: 코드가 실제로 실행되고, 앱이 실제로 열리고, 테스트가 실제로 돌아가는 곳 |

ChatGPT Web이 GitHub와 개인용 컴퓨터에 연결되고, *코드 작성 → 실제 머신에서 실행 → 실제 화면 확인 → 수정 → 커밋*이라는 루프가 **사람 대신 완전히 자동으로** 돌아갔습니다.

그 루프의 대표작이 위의 데모입니다. muse.ai를 처음부터 클론하고, 원본과 화면 단위로 대조하며 검증했습니다.

- 🎯 **프론트엔드 충실도: 99%** — 레이아웃, 간격, 타이포그래피, 상호작용이 원본과 픽셀 단위로 일치
- ⚙️ **백엔드 커버리지: 60% 이상** — 에이전트 루프, 도구, 메모리, 스케줄링, 승인, 보안 모델 재구현
- ⏱️ **총 소요 시간: 하루 남짓** — 빈 저장소에서 동작하는 셀프 호스팅 제품까지

Agentic Windows MCP는 그 삼총사의 "개인용 컴퓨터" 다리를 현실로 만든 구성 요소입니다. **설치하면, 당신이 쓰는 어떤 에이전트든 당신의 머신에 대해 같은 힘을 갖게 됩니다.**

---

## 💡 핵심 아이디어 — 세 개의 기둥

AI 에이전트 혼자서는 *말*밖에 할 수 없습니다. PC 혼자서는 그저 *앉아* 있을 뿐입니다. **Agentic Windows MCP**는 둘 — 그리고 GitHub — 을 하나의 완전한 작업 기계로 이어주는 다리입니다.

| 기둥 | 역할 | 위치 |
|---|---|---|
| **1. GitHub** | 소스 허브: 동기화, 버전 히스토리, 모든 변경의 만남의 장소 | GitHub 클라우드 |
| **2. AI 에이전트** | "두뇌": Muse, ChatGPT, Claude, Gemini, DeepSeek… 분석하고, 결정하고, 코드를 쓰고, 명령을 내림 | 웹, 휴대폰 앱, 다른 머신 — 어디에서든 |
| **3. 당신의 컴퓨터 / 서버** | "손 + 실제 작업장": 프로젝트가 여기 있고, 코드가 여기서 돌고, 실제 앱이 여기서 열림 — Agentic Windows MCP가 실행을 위해 여기에 설치됨 | 집 PC, 사무실 머신, Windows VPS… |

세 기둥은 **지리적으로 완전히 독립**되어 있으며, 단 두 개의 파이프로만 연결됩니다. **MCP 프로토콜**(에이전트 ⇄ 당신의 머신)과 **Git**(당신의 머신 ⇄ GitHub)입니다. 그래서:

- 카페에 앉아 휴대폰으로 ChatGPT를 열고 → 명령을 내리면 → 에이전트가 집에 있는 PC를 조종합니다. 프로젝트를 열고, 코드를 수정하고, 테스트를 돌리고, 커밋해서 GitHub에 푸시합니다. 집에 도착하면 모든 게 끝나 있습니다.
- 또는 세 가지를 **한 대의 머신**으로 합칠 수도 있습니다. 에이전트 앱도 같은 PC, 코드도 같은 PC — Agentic Windows MCP는 에이전트가 마치 앞에 앉은 프로그래머처럼 그 PC를 조작하게 합니다.

---

## ✨ 주요 기능

### 🖥️ 완전한 데스크톱 제어
- **사람 같은 마우스 & 키보드**: 클릭, 더블/우클릭, 다지점 드래그 경로, 누르고 있기, 스와이프, 유니코드 텍스트 입력, 단축키 조합, 스크롤 — 픽셀 단위 정밀도까지.
- **UI 자동화 트리로 화면 읽기**: 활성 창의 모든 요소(버튼, 입력란, 라벨…)를 담은 "스냅샷"과 번호가 매겨진 스크린샷 주석 — 에이전트가 **비전 모델 없이** UI를 이해합니다.
- **창 & 앱 관리**: 앱 실행, 창 전환/닫기/크기 조정, 다중 모니터, 가상 데스크톱, 영역 스크린샷.
- **클립보드, 알림, 디스플레이**: 클립보드 읽기/쓰기, 시스템 알림 전송, 화면 관리.

### ⚙️ 시스템 자동화
- **제한 없는 PowerShell**: 명령 실행, 소프트웨어 설치, 시스템 관리.
- **FileSystem**: 파일 & 폴더 읽기/쓰기/목록/이동 — 소스 코드를 직접 읽고 수정.
- **Registry & Process**: 레지스트리 조회/수정, 프로세스 목록 및 종료.
- **Scrape**: 웹 페이지 콘텐츠 다운로드 및 추출.

### 🌐 진짜 DevTools(F12) 같은 브라우저 검사
브라우저 도구 세트는 **CDP(Chrome DevTools Protocol)** 를 통해 이미 열려 있는 Chrome/Edge/Brave에 연결됩니다.
- 탐색, 탭 관리, DOM 스냅샷, 요소 쿼리, 시각적 하이라이트, 임의의 JavaScript 실행.
- 심층 CSS: 계산된 스타일, **일치하는 규칙**(어떤 규칙이 이기는지, 어느 스타일시트에서 왔는지), `::before`/`::after`, 애니메이션 — 그리고 **접근성 트리**.
- **심층 네트워크**: 응답 본문, 요청 페이로드, 단계별 타이밍(DNS/TCP/TLS/TTFB), 전송 크기, 리디렉션 체인, 실패한 요청, WebSocket 프레임, TLS 인증서 상세.
- **애플리케이션 스토리지**: 쿠키(**HttpOnly** 포함), local/session 스토리지, IndexedDB, Cache Storage, Service Worker.
- **소스**: 모든 스크립트/스타일시트의 목록 및 원문 읽기.

### 👨‍💻 내 PC에서 하는 원격 코딩
도구들을 조합하면 에이전트가 당신의 PC에서 코딩 작업의 전 과정을 수행할 수 있습니다. IDE/프로젝트 폴더 열기 → 코드 읽기 → 파일 수정 → PowerShell로 빌드 & 테스트 → 브라우저를 열어 DevTools로 결과 확인 → 수정 → `git commit` & `git push`로 GitHub에 올리기. 당신은 휴대폰으로도 명령과 감독만 하면 됩니다.

### 🛡️ 안전과 제어권은 당신의 것
- **항상 켜져 있는 시각적 표시**: AI가 제어 중일 때 화면에는 **빛나는 테두리** + 상단 가장자리의 알림 패널 + 커서 주변의 오라가 표시됩니다. "몰래" 제어하는 일은 결코 없습니다.
- **제어권 회수 키**: **`Ctrl + Alt + Shift + Space`** 를 누르면 즉시 제어권을 되찾습니다. 에이전트는 당신이 충분히 유휴 상태가 된 뒤에만 재개합니다.
- **3단계 제어 정책**(`relaxed` / `balanced` / `strict`): strict에서는 *모든* 동작이 표시와 함께 제어 게이트를 통과합니다.
- **에이전트 우선 모드**(선택): 에이전트는 일반적인 마우스/키보드 입력에 방해받지 않습니다 — 위의 단축키만이 제어권을 가져갈 수 있습니다.
- **필수 인증**: `Authorization: Bearer <token>` 헤더의 API 키 또는 OAuth. 엔드포인트는 사설 네트워크(Tailscale/WireGuard)나 HTTPS로만 노출하세요.
- **기본적으로 텔레메트리 없음**: 소스에는 추적 키가 포함되어 있지 않습니다. 사용 분석은 `POSTHOG_API_KEY` 환경 변수를 직접 설정할 때만 활성화됩니다.

---

## 🏗️ 워크플로 & 아키텍처

<div align="center">

<img src="assets/workflow.png" alt="워크플로: 도구 호출 한 번의 전 과정" width="100%">

*도구 호출 한 번의 전 과정 — 요청 → 계획 → 연결 → 인증 → 제어 게이트 → 실행 → 결과. [인터랙티브 버전](assets/workflow.html).*

<img src="assets/architecture.png" alt="런타임 아키텍처" width="100%">

*런타임 아키텍처 — 전송 + 인증, FastMCP 서버, 제어 플레인, 그리고 Windows 위의 데스크톱 / 시스템 / 브라우저 레이어. [인터랙티브 버전](assets/architecture.html).*

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

주요 소스는 `src/agentic_windows_mcp/`(`desktop/`, `browser/`, `tools/`, `infrastructure/`, `filesystem/`, `powershell/`, `registry/`, `uia/`…)에 있습니다.

---

## 🚀 설치 (제어될 Windows 머신에)

**요구 사항**: Windows 10/11 · Python 3.12+ · [uv](https://docs.astral.sh/uv/) · Git.

### 옵션 1 — PyPI에서 (가장 빠름)

```powershell
pip install agentic-windows-mcp
# 또는 uv로 격리 설치:
uv tool install agentic-windows-mcp
```

### 옵션 2 — 소스에서

```powershell
git clone https://github.com/homenewstar/agentic-windows-mcp.git
cd agentic-windows-mcp
uv sync
```

> 브라우저/DevTools 도구는 CDP를 통해 기존 Chrome/Edge와 통신합니다. 서버가 자체 Chromium을 실행하게 하려면 `playwright install chromium`을 한 번 실행하세요.

### 서버 실행

**HTTP 모드(원격 에이전트 / 네트워크용)** — 인증 키와 함께:

```powershell
# 인증 키 생성/교체 (~/.agentic-windows-mcp/ 에 저장)
agentic-windows-mcp auth

# HTTP로 MCP 서버 시작
agentic-windows-mcp serve --transport streamable-http --host 0.0.0.0 --port 8000
# 엔드포인트: http://<PC-IP>:8000/mcp
```

(소스에서 설치한 경우 명령 앞에 `uv run`을 붙이세요.)

**stdio 모드(같은 머신의 에이전트용**, 예: Claude Desktop, Cursor, VS Code): 아래 연결 설정을 보세요 — 클라이언트가 `uvx`로 서버를 직접 실행합니다.

### Windows와 함께 시작

```powershell
agentic-windows-mcp install     # "agentic-windows-mcp-server" 예약 작업 생성
agentic-windows-mcp uninstall   # 작업 제거 및 서버 중지
```

### 엔드포인트를 안전하게 노출하기

포트를 인터넷에 직접 열지 마세요. 다음 중 하나를 사용하세요.

- **Tailscale**(권장): PC와 기기 모두에 Tailscale을 설치한 뒤 `tailscale serve --bg --https=443 http://127.0.0.1:8000` — 엔드포인트는 당신의 사설 tailnet 안에서만 보입니다.
- **WireGuard / 자체 호스팅 VPN**, 또는 인증이 있는 HTTPS 리버스 프록시(Caddy/Nginx).

### 설정

설정 파일은 `~/.agentic-windows-mcp/config.toml`에 있습니다.

```toml
[server]
# 기본 host / port / transport...

[control]
enabled = true
policy = "strict"        # relaxed | balanced | strict
idle_timeout_seconds = 10
```

---

## 🔌 AI 에이전트 연결하기

일반 원칙: **MCP(Model Context Protocol)를 지원하는 모든 애플리케이션**이 stdio(같은 머신) 또는 Streamable HTTP(네트워크, `Authorization: Bearer <API_KEY>` 헤더와 함께)로 연결할 수 있습니다.

### Muse(muse.ai) & 웹/앱 에이전트
웹 기반 에이전트는 당신의 머신에 소프트웨어를 설치하지 않습니다 — Agentic Windows MCP의 HTTP 엔드포인트에 연결합니다.

1. 위와 같이 서버를 HTTP 모드로 실행하고 Tailscale/HTTPS로 노출합니다.
2. 에이전트에서 URL `https://<당신의-머신>/mcp`와 인증 키로 **커스텀 커넥터 / MCP 서버**를 추가합니다.
3. 에이전트는 40개 도구를 모두 볼 수 있고, 프로젝트 열기, 코드 수정, GitHub 푸시까지 당신의 PC를 조종할 수 있습니다.

### ChatGPT
ChatGPT는 **개발자 모드 / 커넥터**를 통해 MCP를 지원합니다.

1. 설정 → 커넥터 → 개발자 모드 활성화 → 새 커넥터 *만들기*.
2. MCP URL(`https://<당신의-머신>/mcp`)과 API 키를 입력합니다.
3. 대화에서 커넥터를 켜고 명령합니다. 예: *"내 PC에서 프로젝트 X를 열고, 테스트를 실행하고, 실패하는 것들을 고쳐줘."*

### Claude(Claude Desktop & Claude Code)
같은 머신 — stdio, Claude가 서버를 직접 실행합니다.

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

다른 머신 — HTTP 엔드포인트를 지정합니다.

```json
{
  "mcpServers": {
    "agentic-windows-mcp": {
      "url": "http://<PC-IP>:8000/mcp",
      "headers": { "Authorization": "Bearer <API_KEY>" }
    }
  }
}
```

Claude Code: `claude mcp add --transport http agentic-windows-mcp http://<PC-IP>:8000/mcp --header "Authorization: Bearer <API_KEY>"`.

### Gemini
- **Gemini CLI**: 위의 Claude 예시와 같은 `mcpServers` 블록을 `~/.gemini/settings.json`에 추가합니다(stdio, 또는 `httpUrl` + `headers`).
- **Google AI Studio / Gemini API**: 앱에 내장된 MCP 클라이언트로 같은 HTTP 엔드포인트를 지정합니다.

### DeepSeek & 그 밖의 모든 에이전트
DeepSeek(및 다른 모델들)는 두 가지 방법으로 연결됩니다.

- **기존 MCP 호스트를 통해**: Cursor, VS Code(Copilot 에이전트 모드), Cline, Continue, Windsurf… — 위의 `mcpServers` 블록만 추가하면 됩니다. 뒤에 있는 모델이 무엇인지는 중요하지 않으며, DeepSeek도 포함됩니다.
- **HTTP로 직접**: 인증 헤더와 함께 `/mcp`에 JSON-RPC를 POST할 수 있는 모든 에이전트 프레임워크(LangChain, LlamaIndex, 직접 작성한 코드…)가 동작합니다.

### 빠른 참조

| 에이전트 / 클라이언트 | 연결 방식 | 비고 |
|---|---|---|
| Muse(웹/앱) | HTTP 커넥터 | 원격 에이전트, Tailscale/HTTPS로 PC 제어 |
| ChatGPT | HTTP 커넥터(개발자 모드) | 개발자 모드를 켜야 함 |
| Claude Desktop / Code | stdio 또는 HTTP | 같은 머신이면 stdio |
| Gemini CLI | stdio 또는 HTTP | `mcpServers` 설정 |
| DeepSeek | MCP 호스트 경유(Cursor/Cline/VS Code…) | 모델은 교체 가능 |
| Cursor / VS Code / Windsurf | stdio 또는 HTTP | `mcpServers` 블록 추가 |

---

## 🧰 도구 세트 (40개 도구)

| 그룹 | 도구 |
|---|---|
| **데스크톱 제어** | `Click`, `Type`, `Move`, `Scroll`, `Shortcut`, `MultiSelect`, `MultiEdit`, `Snapshot`, `Screenshot`, `Wait`, `WaitFor` |
| **앱 & 시스템** | `App`, `PowerShell`, `FileSystem`, `Registry`, `Process`, `Clipboard`, `Notification`, `DisplayInventory`, `Scrape` |
| **제어 게이트** | `ControlStatus` (현재 누가 제어권을 가졌는지, 어떤 정책이 적용 중인지) |
| **브라우저(CDP)** | `Browser_Connect`, `Browser_Navigate`, `Browser_ManageTabs`, `Browser_GetPageSnapshot`, `Browser_QueryElements`, `Browser_Interact`, `Browser_TakeScreenshot`, `Browser_Highlight`, `Browser_EvaluateScript` |
| **심층 DevTools** | `Browser_GetCssStyles`, `Browser_GetComputedStyle`, `Browser_GetMatchedStyles`, `Browser_GetAccessibilityTree`, `Browser_ListConsoleMessages`, `Browser_ListNetworkRequests`, `Browser_GetNetworkDetails`, `Browser_GetStorage`, `Browser_ListPageSources`, `Browser_GetSourceText` |

---

## 💬 에이전트에게 내릴 명령 예시

- *"내 PC의 `D:\code\shop-web` 프로젝트를 열고, 테스트를 실행하고, 실패하는 것을 모두 고친 뒤, 커밋해서 GitHub에 푸시해줘."*
- *"내 홈페이지가 느리게 로딩돼 — Chrome에서 열고 네트워크 탭을 검사해서 어떤 요청이 왜 느린지 알려줘."*
- *"Downloads 폴더의 매출 보고서 스프레드시트를 읽고, 월별 매출을 요약해서 새 파일로 저장해줘."*
- *"이 머신에 Node.js와 VS Code를 설치하고, 내 이름과 이메일로 git을 설정해줘."*
- *"메모 앱을 열고, 클립보드의 오늘 회의 내용을 붙여넣고 저장해줘."*

---

## 📈 트렌드 추적

GitHub 저장소의 트렌딩 히스토리를 기록하는 플랫폼 **[Trendshift](https://trendshift.io)** 에서 이 프로젝트를 팔로우하세요.

<div align="center">

[![Trendshift](https://img.shields.io/badge/Trendshift-트렌딩%20저장소%20탐색-ff6b35?style=for-the-badge)](https://trendshift.io)

</div>

> 이 저장소가 GitHub Trending에 오르면, 실시간 Trendshift 배지(순위 & 히스토리)가 바로 여기에 삽입됩니다.

---

## 🤝 기여하기

기여를 환영합니다 — 먼저 [CONTRIBUTING.md](CONTRIBUTING.md)를 읽어 주세요. 요약: 모든 작업은 `main` 브랜치에서 이루어지며, 변경이 반영되기 전에 테스트 스위트가 통과해야 합니다(`uv run pytest`).

## 🔒 보안

이 서버는 실행 중인 머신에서 실제적이고 되돌릴 수 없는 동작을 수행할 수 있습니다. 배포 전에 [SECURITY.md](SECURITY.md)를 읽고, 취약점은 공개 이슈가 아닌 [GitHub Security Advisories](https://github.com/homenewstar/agentic-windows-mcp/security/advisories/new)를 통해 비공개로 신고해 주세요.

## 📄 라이선스

**Apache License 2.0** 으로 배포됩니다 — © 2026 **Vu Manh Ruy**. 자세한 내용은 [LICENSE.md](LICENSE.md)와 [NOTICE](NOTICE)를 참조하세요.
