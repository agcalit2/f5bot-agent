import os
from playwright.async_api import Page


async def login(page: Page) -> None:
    await page.goto("https://f5bot.com/login")
    await page.fill("#email", os.environ["F5BOT_USERNAME"])
    await page.fill('#password', os.environ["F5BOT_PASSWORD"])
    async with page.expect_navigation():
        await page.click('button[type="submit"]')


async def reenable_keywords(page: Page) -> list[str]:
    await page.goto("https://f5bot.com/dash")
    await page.wait_for_load_state("networkidle")

    # Disabled keywords have td[data-sort="0"] in the Enabled column.
    # Clicking the input[type="image"] inside submits the /toggle-enabled form.
    reenabled: list[str] = []

    while True:
        disabled_cells = page.locator('td[data-sort="0"]')
        count = await disabled_cells.count()
        if count == 0:
            break

        cell = disabled_cells.first
        row = cell.locator("xpath=ancestor::tr[1]")
        keyword = (await row.locator("td").first.text_content() or "").strip()

        async with page.expect_navigation():
            await cell.locator('input[type="image"]').click()

        reenabled.append(keyword)

    return reenabled
