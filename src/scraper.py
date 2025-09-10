import asyncio
from typing import List, Dict
from collections import defaultdict
from random import uniform

from playwright.async_api import async_playwright, Page

TRP_LINK = ("https://broneering.politsei.ee/MakeReservation/SelectLocation?"
            "serviceId=H_GGs4WzRUW23mKUtDVIcA")
AVAILABLE_DAYS = {i: defaultdict(list) for i in range(5)}


async def get_browser(chromium: async_playwright) -> async_playwright:
    return await chromium.launch(
        headless=True,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-accelerated-2d-canvas',
            '--disable-gpu',
            '--window-size=1920,1080',
            '--start-maximized',
            '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ]
    )


async def get_context(browser: async_playwright) -> async_playwright:
    return await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                locale='et-EE',  # Estonian locale
                timezone_id='Europe/Tallinn',

                extra_http_headers={
                    'Accept-Language': 'et-EE,et;q=0.9,en;q=0.8',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
            )


async def add_page_script(page: async_playwright) -> None:
    await page.add_init_script("""
                                Object.defineProperty(navigator, 'webdriver', {
                                    get: () => undefined
                                });
    
                                // Override the permissions API
                                const originalQuery = window.navigator.permissions.query;
                                window.navigator.permissions.query = (parameters) => (
                                    parameters.name === 'notifications' ?
                                        Promise.resolve({ state: Notification.permission }) :
                                        originalQuery(parameters)
                                );
    
                                // Override plugins
                                Object.defineProperty(navigator, 'plugins', {
                                    get: () => [1, 2, 3, 4, 5]
                                });
    
                                // Override languages
                                Object.defineProperty(navigator, 'languages', {
                                    get: () => ['et-EE', 'et', 'en-US', 'en']
                                });
                            """)


async def get_branches(page: Page) -> tuple:
    await page.wait_for_selector(".btn.btn-light.btn-lg.btn-block.no-shadow",
                                 timeout=30_000)
    buttons = page.locator(".btn.btn-light.btn-lg.btn-block.no-shadow")

    count = await buttons.count()
    print(f"Found {count} branches")
    return buttons, count


async def open_calendar(page: Page) -> None:
    button = page.get_by_role("button", name="Edasi")
    await button.click()


async def get_available_days(page: Page) -> List[str]:
    await page.wait_for_selector(".day", timeout=45_000)
    days = page.locator(".day")
    count = await days.count()
    available_days = set()

    for i in range(count):
        day_el = days.nth(i)
        classes = await day_el.get_attribute("class") or ""
        text = (await day_el.inner_text()).strip()
        if text.isdigit() and "disabled" not in classes:
            available_days.add(text)
    return list(available_days)


async def open_next_month(page: Page) -> None:
    link = page.locator("a:has-text('järgmine kuu')")
    await link.wait_for(state="visible", timeout=30_000)
    await asyncio.sleep(uniform(1, 3))
    await link.click()


def check_new_available_days(available_days: List[str],
                             branch: int, month: int) -> List[str]:
    new_available_days = []
    for day in available_days:
        if day not in AVAILABLE_DAYS[branch][month]:
            AVAILABLE_DAYS[branch][month].append(day)
            new_available_days.append(day)
    for day in AVAILABLE_DAYS[branch][month]:
        if day not in available_days:
            AVAILABLE_DAYS[branch][month].remove(day)
    return new_available_days


async def search_branch(page: Page, branch: int) -> Dict:
    """
    Returns a dictionary with structure:
    {
        'branch': branch_number,
        'months': {
            1: ['1', '5', '15'],  # days available in month 1
            2: ['3', '20'],       # days available in month 2
            ...
        }
    }
    """
    branch_result = {
        'branch': branch + 1,
        'months': {}
    }

    try:
        await page.goto(TRP_LINK)
        buttons, count = await get_branches(page)

        # Click the specific branch
        await buttons.nth(branch).click()
        await open_calendar(page)

        # Check first 3 months
        for month in range(3):
            available_days = await get_available_days(page)
            print(f"{branch + 1}. branch {month + 1}. month available days: "
                  f"{available_days}")

            if available_days:
                new_available_days = check_new_available_days(available_days,
                                                              branch, month)
                # Store all available days for this month (not just new ones)
                branch_result['months'][month + 1] = available_days

                if new_available_days:
                    print(f"Branch {branch + 1}, Month {month + 1} found NEW days: {new_available_days}")

            await open_next_month(page)

        # Check 4th month
        available_days = await get_available_days(page)
        print(f"{branch + 1}. branch 4. month available days: {available_days}")
        if available_days:
            new_available_days = check_new_available_days(available_days,
                                                          branch, 3)
            # Store all available days for month 4
            branch_result['months'][4] = available_days

            if new_available_days:
                print(f"Branch {branch + 1}, Month 4 found NEW days: {new_available_days}")

        print(f"Branch {branch + 1} completed")
        return branch_result

    except Exception as e:
        print(f"Branch {branch + 1} failed with error: {e}")
        return branch_result  # Return what we have so far


async def run_search():
    async with async_playwright() as playwright:
        chromium = playwright.chromium
        browser = await get_browser(chromium)

        # Create 5 tabs in the same browser and search concurrently
        tasks = []
        pages = []
        for branch in range(5):
            context = await get_context(browser)

            page = await context.new_page()

            # Remove webdriver property
            await add_page_script(page)

            pages.append(page)
            await asyncio.sleep(uniform(1, 2))

            # Create task explicitly using asyncio.create_task()
            task = asyncio.create_task(search_branch(page, branch))
            tasks.append(task)

        # Wait for ALL tasks to complete (not just the first one)
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process all results into final structure
        final_results = {}
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"Branch {i + 1} encountered an error: {result}")
            elif result and result.get('months'):
                # Only include branches that have available days
                final_results[f"Branch {result['branch']}"] = result['months']

        # Give a moment before closing
        await asyncio.sleep(6)
        await browser.close()

        return final_results


if __name__ == '__main__':
    result = asyncio.run(run_search())
    if result:
        print("\n=== FINAL RESULTS ===")
        for branch, months in result.items():
            print(f"\n{branch}:")
            for month, days in months.items():
                print(f"  Month {month}: {days}")
    else:
        print("No available days found in any branch")
        print("Returning: {}")