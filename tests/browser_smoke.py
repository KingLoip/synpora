"""Real Chromium smoke test for the generated SYNPORA frontend."""
import asyncio
from playwright.async_api import async_playwright, expect


BASE_URL = "http://127.0.0.1:8765/"


async def smoke_normal_flow(browser):
    context = await browser.new_context(viewport={"width": 1440, "height": 1000})
    page = await context.new_page()
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    response = await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    assert response and response.status == 200, "Homepage did not return HTTP 200"
    await page.wait_for_timeout(2200)

    assert await page.locator("html").get_attribute("lang") == "en"
    await expect(page.locator("#synpora-nav")).to_have_count(1)
    assert "Which use of your electricity creates the most economic value" in await page.locator("body").inner_text()
    await expect(page.get_by_role("button", name="Load demo farm")).to_have_count(1)
    assert await page.locator('input[placeholder*="Railway Bootstrap"]').count() == 0

    await page.get_by_role("button", name="Load demo farm").click()
    await expect(page.locator("#synpora-panel")).to_be_visible()
    assert "Illustrative demo data" in await page.locator("#synpora-panel").inner_text()

    await page.locator("#sp-energy-kwh").fill("100")
    await page.locator("#sp-energy-price").fill("")
    await page.locator("#sp-sim").click()
    await expect(page.locator(".sp-scenario")).to_be_visible()
    assert "ILLUSTRATIVE DEMO SCENARIO" in await page.locator(".sp-scenario").inner_text()
    assert "not live market data" in (await page.locator(".sp-scenario").inner_text()).lower()

    await page.locator("#sp-account-btn").click()
    await expect(page.locator("#sp-password")).to_be_visible()
    assert await page.locator("#sp-auth-status").count() == 1
    assert not page_errors, "Browser JavaScript errors: " + "; ".join(page_errors)
    await context.close()


async def smoke_xss_regression(browser):
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    await context.add_init_script("""
      localStorage.setItem("synpora_account", JSON.stringify({
        email: "browser-test@example.invalid",
        farm: '<img id="xss-probe" src=x onerror=alert(1)>',
        token: "not-a-real-token"
      }));
    """)
    page = await context.new_page()
    response = await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    assert response and response.status == 200
    await page.wait_for_timeout(2200)
    assert await page.locator("#xss-probe").count() == 0, "Untrusted workspace name was interpreted as HTML"
    label = await page.locator("#sp-account .sp-mini").inner_text()
    assert "<img id=\"xss-probe\"" in label, "Escaped workspace text was not preserved as text"
    await context.close()


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            await smoke_normal_flow(browser)
            await smoke_xss_regression(browser)
        finally:
            await browser.close()
    print("SYNPORA Chromium browser smoke: PASS")


if __name__ == "__main__":
    asyncio.run(main())
