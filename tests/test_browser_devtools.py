import asyncio
import urllib.parse
from agentic_windows_mcp.browser.devtools_manager import BrowserDevToolsManager

TEST_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>DevTools Test Page</title>
    <style>
        body { font-family: sans-serif; background-color: #f0f4f8; margin: 0; padding: 20px; }
        .card {
            display: flex;
            flex-direction: column;
            width: 350px;
            padding: 16px;
            background-color: #ffffff;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .btn {
            background-color: #2563eb;
            color: #ffffff;
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div id="main-card" class="card">
        <h1 id="title">Testing DevTools Capabilities</h1>
        <p id="desc">Verify DOM, CSS, JS evaluation and Network.</p>
        <button id="action-btn" class="btn" onclick="handleClick()">Click Me</button>
        <span id="result-status">Idle</span>
    </div>

    <script>
        console.log("Page initialized successfully.");
        function handleClick() {
            document.getElementById("result-status").innerText = "Clicked!";
            console.warn("User triggered handleClick action!");
        }
    </script>
</body>
</html>"""

async def test_devtools_lifecycle_and_extraction():
    manager = BrowserDevToolsManager.get_instance()
    
    # 1. Connect or launch standalone headless browser
    conn_res = await manager.connect_or_launch(headless=True)
    assert conn_res["status"] == "connected"
    print("\n[OK] Browser connected/launched:", conn_res["mode"])

    # 2. Navigate to test page using data URL
    data_url = "data:text/html;charset=utf-8," + urllib.parse.quote(TEST_HTML)
    nav_res = await manager.navigate(data_url, wait_until="load")
    assert nav_res["title"] == "DevTools Test Page"
    print("[OK] Navigation verified:", nav_res["title"])

    # 3. Dissect HTML DOM (Snapshot)
    snapshot = await manager.get_page_snapshot(selector="#main-card")
    assert "Testing DevTools Capabilities" in snapshot["content"]
    assert "action-btn" in snapshot["content"]
    print("[OK] DOM Snapshot verified (elements extracted accurately)")

    # 4. Dissect CSS & Box Model Layout
    css_res = await manager.get_css_styles("#main-card", properties=["display", "flex-direction", "background-color", "width"])
    assert css_res["computedStyles"]["display"] == "flex"
    assert css_res["computedStyles"]["flex-direction"] == "column"
    assert css_res["boundingBox"]["width"] >= 350
    assert css_res["isVisible"] is True
    print("[OK] CSS & Layout extraction verified:", css_res["computedStyles"], "Box:", css_res["boundingBox"])

    # 5. Execute arbitrary JavaScript in page context
    js_result = await manager.evaluate_script("() => document.getElementById('title').innerText")
    assert js_result == "Testing DevTools Capabilities"
    print("[OK] JavaScript evaluation verified:", js_result)

    # 6. Query elements by selector
    elements = await manager.query_elements("button, span", attributes=["id", "class"])
    assert len(elements) >= 2
    assert elements[0]["tagName"] == "button"
    print(f"[OK] Query elements verified: found {len(elements)} elements")

    # 7. UI Interaction: Click button and verify state change
    click_res = await manager.click("#action-btn")
    assert click_res["status"] == "success"
    
    # Wait for DOM state update
    updated_status = await manager.evaluate_script("() => document.getElementById('result-status').innerText")
    assert updated_status == "Clicked!"
    print("[OK] UI Interaction verified: state changed to 'Clicked!'")

    # 8. Check console logs
    logs = manager.list_console_logs()
    assert any("Page initialized successfully" in log["text"] for log in logs)
    assert any("User triggered handleClick action" in log["text"] for log in logs)
    print(f"[OK] Console message capture verified: {len(logs)} logs captured")

    # 9. Clean shutdown
    await manager.close()
    print("[OK] Browser shutdown cleanly.")

if __name__ == "__main__":
    asyncio.run(test_devtools_lifecycle_and_extraction())
