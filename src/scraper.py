import asyncio
from typing import List, Dict
from collections import defaultdict
from random import uniform
import os

from playwright.async_api import async_playwright, Page, Browser

TRP_LINK = ("https://broneering.politsei.ee/MakeReservation/SelectLocation?"
            "serviceId=H_GGs4WzRUW23mKUtDVIcA")
AVAILABLE_DAYS = {i: defaultdict(list) for i in range(5)}


async def get_branches(page: Page) -> tuple:
    await page.wait_for_selector(".btn.btn-light.btn-lg.btn-block.no-shadow",
                                 timeout=60_000)  # Increased timeout for cloud
    buttons = page.locator(".btn.btn-light.btn-lg.btn-block.no-shadow")

    count = await buttons.count()
    print(f"Found {count} branches")
    return buttons, count


async def open_calendar(page: Page) -> None:
    button = page.get_by_role("button", name="Edasi")
    await button.click()
    # Add extra wait after clicking to ensure page transition completes
    await asyncio.sleep(uniform(3, 5))  # Increased for cloud latency


async def get_available_days(page: Page) -> List[str]:
    # Try multiple selectors and wait strategies
    try:
        # First try to wait for the calendar container
        await page.wait_for_selector(".calendar", timeout=20_000)
    except:
        pass

    # Add a longer delay for cloud environments
    await asyncio.sleep(uniform(2, 4))

    # Try waiting for network idle state with longer timeout
    try:
        await page.wait_for_load_state("networkidle", timeout=30_000)
    except:
        print("Network idle timeout - continuing anyway")

    # Now wait for the days with a longer timeout
    await page.wait_for_selector(".day", timeout=60_000)  # Increased timeout
    days = page.locator(".day")

    # Wait a bit more to ensure all days are loaded
    await asyncio.sleep(2)

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
    await link.wait_for(state="visible", timeout=45_000)
    await asyncio.sleep(uniform(3, 5))  # Increased delay for cloud
    await link.click()
    # Wait for the calendar to update
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except:
        pass
    await asyncio.sleep(uniform(2, 3))


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


async def search_branch(page: Page, branch: int, retry_count: int = 3) -> Dict:
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

    for attempt in range(retry_count):
        try:
            print(f"Branch {branch + 1}, Attempt {attempt + 1}")

            # Navigate with retry logic
            for nav_attempt in range(3):
                try:
                    await page.goto(TRP_LINK, wait_until="domcontentloaded", timeout=60_000)
                    break
                except Exception as nav_e:
                    print(f"Navigation attempt {nav_attempt + 1} failed: {nav_e}")
                    if nav_attempt == 2:
                        raise
                    await asyncio.sleep(5)

            # Wait for page to fully load
            await asyncio.sleep(uniform(3, 5))

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

            print(f"Branch {branch + 1} completed successfully")
            return branch_result

        except Exception as e:
            print(f"Branch {branch + 1}, Attempt {attempt + 1} failed with error: {e}")
            if attempt < retry_count - 1:
                print(f"Retrying branch {branch + 1}...")
                await asyncio.sleep(5)
                # Reload the page for next attempt
                try:
                    await page.reload()
                except:
                    pass
            else:
                print(f"Branch {branch + 1} failed after {retry_count} attempts")
                return branch_result


async def run_search_sequential():
    """Sequential version - more reliable on cloud platforms with limited resources"""
    async with async_playwright() as playwright:
        chromium = playwright.chromium

        # Cloud-optimized browser configuration
        browser = await chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',  # Critical for cloud/Docker
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
                '--no-sandbox',  # Required for most cloud environments
                '--disable-setuid-sandbox',
                '--disable-accelerated-2d-canvas',
                '--disable-gpu',  # Important for headless
                '--window-size=1920,1080',
                '--single-process',  # Helps with resource constraints
                '--no-zygote',  # Helps with container environments
                '--memory-pressure-off',
                '--max_old_space_size=4096',  # Memory limit
                '--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ]
        )

        final_results = {}

        # Process branches sequentially to avoid resource issues
        for branch in range(5):
            try:
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    locale='et-EE',
                    timezone_id='Europe/Tallinn',
                    extra_http_headers={
                        'Accept-Language': 'et-EE,et;q=0.9,en;q=0.8',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                    }
                )

                page = await context.new_page()

                # Remove webdriver property
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });

                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });

                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['et-EE', 'et', 'en-US', 'en']
                    });
                """)

                # Search the branch with retry logic
                result = await search_branch(page, branch)

                if result and result.get('months'):
                    final_results[f"Branch {result['branch']}"] = result['months']

                # Clean up context after each branch
                await context.close()

                # Delay between branches
                await asyncio.sleep(3)

            except Exception as e:
                print(f"Critical error processing branch {branch + 1}: {e}")
                continue

        await browser.close()
        return final_results


async def run_search():
    """Main function that chooses strategy based on environment"""
    # Check if running in cloud/container environment
    is_cloud = os.environ.get('RAILWAY_ENVIRONMENT') or \
               os.environ.get('RENDER') or \
               os.environ.get('HEROKU') or \
               os.environ.get('FLY_APP_NAME') or \
               os.path.exists('/.dockerenv')

    if is_cloud:
        print("Detected cloud environment - using sequential processing")
        return await run_search_sequential()
    else:
        print("Local environment - using parallel processing")
        # Original parallel version
        async with async_playwright() as playwright:
            chromium = playwright.chromium

            browser = await chromium.launch(
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
                    '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                ]
            )

            tasks = []
            pages = []
            for branch in range(5):
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    locale='et-EE',
                    timezone_id='Europe/Tallinn',
                    extra_http_headers={
                        'Accept-Language': 'et-EE,et;q=0.9,en;q=0.8',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                    }
                )

                page = await context.new_page()

                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });

                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });

                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['et-EE', 'et', 'en-US', 'en']
                    });
                """)

                pages.append(page)
                await asyncio.sleep(uniform(1, 2))

                task = asyncio.create_task(search_branch(page, branch))
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)

            final_results = {}
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"Branch {i + 1} encountered an error: {result}")
                elif result and result.get('months'):
                    final_results[f"Branch {result['branch']}"] = result['months']

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