from playwright.sync_api import sync_playwright
OUT = "/var/lib/freelancer/projects/40674900/"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.set_viewport_size({"width": 1280, "height": 900})
    pg.goto("https://kdx-sa.com/", wait_until="networkidle", timeout=90000)
    pg.wait_for_timeout(4000)
    pg.mouse.wheel(0, 1400)
    pg.wait_for_timeout(3000)
    pg.screenshot(path=OUT + "shop-new-batch.png")
    print(pg.title())
    b.close()
