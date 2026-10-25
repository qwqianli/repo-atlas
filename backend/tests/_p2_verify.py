"""P2 验收：用真实浏览器点一遍，截图存盘。

运行依赖 playwright（它是验收工具，不是项目依赖，所以不进 requirements.txt）：
    pip install playwright && playwright install chromium
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(r"C:/Users/Administrator/AppData/Local/Temp/p2shots")
OUT.mkdir(parents=True, exist_ok=True)
REPO = r"C:/Users/Administrator/WorkBuddy/2026-10-24-13-14-04/qa-lab"

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
