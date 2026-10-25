"""用真实浏览器手动点一遍页面，截图存盘。

这是开发期的辅助脚本，不是自动化测试（不会被 pytest 收集）。

用法：
    pip install playwright && playwright install chromium
    python tests/manual_ui_check.py <要分析的仓库路径>
    例：python tests/manual_ui_check.py ~/repos/qa-lab

前提：后端（5000）与前端（5173）已经起好。
"""
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

if len(sys.argv) < 2:
    raise SystemExit("用法：python tests/manual_ui_check.py <要分析的仓库路径>")

# expanduser 让 ~ 在 Windows / macOS / Linux 上都能展开（直接写 r"~/xxx" 在 Windows 上不展开）
REPO = str(Path(sys.argv[1]).expanduser().resolve())
OUT = Path(tempfile.gettempdir()) / "ui_shots"
OUT.mkdir(parents=True, exist_ok=True)

errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

    pg.goto("http://localhost:5173/", wait_until="networkidle")
    pg.screenshot(path=str(OUT / "1_home.png"))

    pg.locator("input.repo-path").fill(REPO)
    pg.locator("button[type=submit]").click()
    pg.wait_for_url("**/tasks/*", timeout=30000)
    print("跳转成功:", pg.url)

    pg.wait_for_selector(".workspace canvas", timeout=90000)
    pg.wait_for_timeout(2500)
    pg.screenshot(path=str(OUT / "2_result.png"))

    print("画布数量:", pg.locator(".workspace canvas").count())
    print("文件树节点数:", pg.locator(".file-tree .tree-row, .file-tree li, .file-tree button").count())

    # 点文件树里的一个文件
    target = pg.get_by_text("page_login.py", exact=False).first
    target.click()
    pg.wait_for_timeout(1000)
    detail = pg.inner_text(".module-detail")
    print("=== 右侧面板内容 ===")
    print(detail[:400])
    pg.screenshot(path=str(OUT / "3_node_selected.png"))

    # 缩放：滚轮
    box = pg.locator(".workspace canvas").first.bounding_box()
    if box:
        pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        pg.mouse.wheel(0, -300)
        pg.wait_for_timeout(600)
        pg.screenshot(path=str(OUT / "4_zoomed.png"))

    # 错误路径：不存在的仓库
    pg.goto("http://localhost:5173/", wait_until="networkidle")
    pg.locator("input.repo-path").fill("C:/no/such/repo")
    pg.locator("button[type=submit]").click()
    pg.wait_for_timeout(3000)
    body = pg.inner_text("body")
    print("=== 错误页是否出现提示 ===")
    print("含'不存在':", "不存在" in body, "| body 前 200 字:", body[:200].replace("\n", " "))
    pg.screenshot(path=str(OUT / "5_error.png"))

    b.close()

print("=== 浏览器控制台错误 ===")
print("\n".join(errors) if errors else "无")
