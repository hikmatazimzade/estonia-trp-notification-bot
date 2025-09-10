import asyncio
from typing import List, Dict, Optional
from collections import defaultdict
from random import uniform
import os
import sys

from playwright.async_api import async_playwright, Page, Browser

TRP_LINK = ("https://broneering.politsei.ee/MakeReservation/SelectLocation?"
            "serviceId=H_GGs4WzRUW23mKUtDVIcA")
AVAILABLE_DAYS = {i: defaultdict(list) for i in range(5)}


async def debug_page_content(page: Page, context: str):
    """Debug helper to understand what's on the page"""
    print(f"\n=== DEBUG {context} ===")
    try:
        # Get page title
        title = await page.title()
        print(f"Page title: {title}")

        # Get URL
        url = page.url
        print(f"Current URL: {url}")

        # Check for any error messages
        error_selectors = [
            ".error", ".alert", ".warning",
            "[class*='error']", "[class*='alert']"
        ]
        for selector in error_selectors:
            try:
                error = await page.locator(selector).first.text_content(timeout=1000)
                if error:
                    print(f"Found error/alert: {error}")
            except:
                pass

        # Check if page is blocked
        body_text = await page.locator("body").text_content(timeout=5000)
        if body_text:
            preview = body_text[:500].replace('\n', ' ')
            print(f"Body preview: {preview}")

            # Check for blocking indicators
            blocking_keywords = ['blocked', 'denied', 'forbidden', 'captcha', 'verify', 'robot']
            for keyword in blocking_keywords:
                if keyword.lower() in body_text.lower():
                    print(f"⚠️  Found blocking keyword: {keyword}")

        # Take screenshot for debugging (Railway logs)
        if os.environ.get('RAILWAY_ENVIRONMENT'):
            screenshot = await page.screenshot(full_page=False)
            print(f"Screenshot taken, size: {len(screenshot)} bytes")

    except Exception as e:
        print(f"Debug error: {e}")
    print("=== END DEBUG ===\n")


async def get_branches(page: Page) -> tuple:
    print("Waiting for branch buttons...")

    # Try multiple selectors
    selectors = [
        ".btn.btn-light.btn-lg.btn-block.no-shadow",
        "button.btn-light",
        "[class*='btn'][class*='light']",
        "button"
    ]

    for selector in selectors:
        try:
            await page.wait_for_selector(selector, timeout=30_000, state="visible")
            buttons = page.locator(selector)
            count = await buttons.count()
            if count > 0:
                print(f"Found {count} elements with selector: {selector}")
                # Filter for actual branch buttons if needed
                if count > 10:  # Too many, probably got all buttons
                    continue
                return buttons, count
        except Exception as e:
            print(f"Selector {selector} failed: {e}")
            continue

    # If nothing worked, debug the page
    await debug_page_content(page, "Failed to find branches")
    raise Exception("Could not find branch buttons")


async def open_calendar(page: Page) -> None:
    print("Opening calendar...")

    # Try multiple ways to find the "Next" button
    selectors = [
        "button:has-text('Edasi')",
        "[role='button']:has-text('Edasi')",
        "button:has-text('Next')",
        "button.btn-primary",
        "[class*='btn'][class*='primary']"
    ]

    clicked = False
    for selector in selectors:
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=5000):
                await button.click()
                print(f"Clicked button with selector: {selector}")
                clicked = True
                break
        except:
            continue

    if not clicked:
        # Try JavaScript click as last resort
        try:
            await page.evaluate("""
                const buttons = Array.from(document.querySelectorAll('button'));
                const edasiButton = buttons.find(b => b.textContent.includes('Edasi'));
                if (edasiButton) {
                    edasiButton.click();
                    return true;
                }
                return false;
            """)
            print("Clicked via JavaScript")
        except:
            raise Exception("Could not click Edasi button")

    # Wait for navigation/calendar to load
    await asyncio.sleep(uniform(3, 5))


async def wait_for_calendar_alternative(page: Page) -> bool:
    """Alternative method to wait for calendar using different strategies"""
    print("Attempting alternative calendar detection...")

    strategies = [
        # Strategy 1: Wait for any calendar-related class
        ("*[class*='calendar']", "calendar class"),
        ("*[class*='datepicker']", "datepicker class"),
        ("*[class*='month']", "month class"),

        # Strategy 2: Wait for table that might be calendar
        ("table", "table element"),

        # Strategy 3: Wait for elements with day-like content
        ("*:has-text('1'):has-text('2'):has-text('3')", "numbered elements"),

        # Strategy 4: Check for date-related attributes
        ("[data-date]", "data-date attribute"),
        ("[aria-label*='date']", "aria-label with date"),
    ]

    for selector, description in strategies:
        try:
            element = page.locator(selector).first
            await element.wait_for(state="visible", timeout=10_000)
            print(f"✓ Found calendar using: {description}")
            return True
        except:
            continue

    return False


async def get_available_days(page: Page) -> List[str]:
    print("Getting available days...")

    # First, try to detect if calendar is present using alternative methods
    calendar_found = await wait_for_calendar_alternative(page)

    if not calendar_found:
        await debug_page_content(page, "Calendar not found")

    # Wait for network to settle
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except:
        print("Network idle timeout - continuing")

    await asyncio.sleep(2)

    # Try multiple selectors for days
    day_selectors = [
        ".day",
        "td.day",
        "[class*='day']",
        "td[data-date]",
        "td[role='gridcell']",
        "button[aria-label*='day']",
        "td:not(.disabled)",
        ".calendar td",
        "table td"
    ]

    available_days = set()

    for selector in day_selectors:
        try:
            # Check if elements exist
            days = page.locator(selector)
            count = await days.count()

            if count == 0:
                continue

            print(f"Found {count} elements with selector: {selector}")

            # Wait for at least one to be visible
            try:
                await days.first.wait_for(state="visible", timeout=5000)
            except:
                continue

            # Process each day
            for i in range(min(count, 42)):  # Max 42 days (6 weeks)
                try:
                    day_el = days.nth(i)

                    # Get text content
                    text = await day_el.text_content(timeout=1000)
                    if not text:
                        continue

                    text = text.strip()

                    # Check if it's a valid day number (1-31)
                    if text.isdigit() and 1 <= int(text) <= 31:
                        # Check if it's disabled
                        classes = await day_el.get_attribute("class") or ""
                        aria_disabled = await day_el.get_attribute("aria-disabled") or "false"

                        # Various ways a day might be disabled
                        is_disabled = (
                                "disabled" in classes.lower() or
                                "inactive" in classes.lower() or
                                "unavailable" in classes.lower() or
                                aria_disabled.lower() == "true"
                        )

                        if not is_disabled:
                            # Additional check: see if it's clickable
                            try:
                                is_clickable = await day_el.is_enabled(timeout=100)
                                if is_clickable:
                                    available_days.add(text)
                            except:
                                # If we can't determine, assume it's available
                                available_days.add(text)

                except Exception as e:
                    continue

            # If we found days, stop trying other selectors
            if available_days:
                print(f"Successfully found {len(available_days)} available days with selector: {selector}")
                break

        except Exception as e:
            print(f"Selector {selector} failed: {e}")
            continue

    # If no days found, try JavaScript extraction
    if not available_days:
        print("Trying JavaScript extraction...")
        try:
            js_days = await page.evaluate("""
                () => {
                    const days = [];
                    // Try to find all clickable day elements
                    const elements = document.querySelectorAll('td, button, div, span');
                    for (const el of elements) {
                        const text = el.textContent.trim();
                        if (/^[0-9]{1,2}$/.test(text)) {
                            const num = parseInt(text);
                            if (num >= 1 && num <= 31) {
                                // Check if element or parent is disabled
                                const isDisabled = 
                                    el.disabled || 
                                    el.classList.contains('disabled') ||
                                    el.getAttribute('aria-disabled') === 'true' ||
                                    (el.parentElement && el.parentElement.classList.contains('disabled'));

                                if (!isDisabled) {
                                    // Check if it has click handlers or is interactive
                                    const hasClick = el.onclick || el.getAttribute('onclick');
                                    const isButton = el.tagName === 'BUTTON';
                                    const hasRole = el.getAttribute('role') === 'button' || el.getAttribute('role') === 'gridcell';

                                    if (hasClick || isButton || hasRole || el.style.cursor === 'pointer') {
                                        days.push(text);
                                    }
                                }
                            }
                        }
                    }
                    return [...new Set(days)];  // Remove duplicates
                }
            """)
            if js_days:
                available_days = set(js_days)
                print(f"JavaScript found {len(available_days)} available days")
        except Exception as e:
            print(f"JavaScript extraction failed: {e}")

    result = list(available_days)
    print(f"Final available days: {result}")

    # If still no days, debug the page
    if not result:
        await debug_page_content(page, "No available days found")

    return result


async def open_next_month(page: Page) -> None:
    print("Opening next month...")

    # Try multiple selectors for next month
    selectors = [
        "a:has-text('järgmine kuu')",
        "button:has-text('järgmine kuu')",
        "*:has-text('järgmine')",
        "a:has-text('next')",
        "button:has-text('next')",
        "[aria-label*='next']",
        ".next-month",
        "[class*='next']"
    ]

    clicked = False
    for selector in selectors:
        try:
            element = page.locator(selector).first
            if await element.is_visible(timeout=3000):
                await element.click()
                print(f"Clicked next month with selector: {selector}")
                clicked = True
                break
        except:
            continue

    if not clicked:
        # Try JavaScript as last resort
        try:
            success = await page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a, button'));
                    const nextLink = links.find(l => 
                        l.textContent.toLowerCase().includes('järgmine') ||
                        l.textContent.toLowerCase().includes('next')
                    );
                    if (nextLink) {
                        nextLink.click();
                        return true;
                    }
                    return false;
                }
            """)
            if success:
                print("Clicked next month via JavaScript")
                clicked = True
        except:
            pass

    if not clicked:
        print("WARNING: Could not click next month button")

    # Wait for calendar to update
    await asyncio.sleep(uniform(3, 5))
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except:
        pass


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
    Search a single branch for available days
    """
    branch_result = {
        'branch': branch + 1,
        'months': {}
    }

    try:
        print(f"\n{'=' * 50}")
        print(f"Starting Branch {branch + 1}")
        print(f"{'=' * 50}")

        # Navigate to the page
        print(f"Navigating to {TRP_LINK}")
        response = await page.goto(TRP_LINK, wait_until="domcontentloaded", timeout=60_000)

        if response and not response.ok:
            print(f"WARNING: Page returned status {response.status}")

        # Wait for initial load
        await asyncio.sleep(uniform(5, 7))

        # Debug initial page state
        await debug_page_content(page, f"Branch {branch + 1} initial page")

        # Get and click branch button
        buttons, count = await get_branches(page)

        print(f"Clicking branch {branch + 1} button...")
        await buttons.nth(branch).click()
        await asyncio.sleep(uniform(3, 5))

        # Open calendar
        await open_calendar(page)

        # Check first 4 months
        for month in range(4):
            print(f"\n--- Branch {branch + 1}, Month {month + 1} ---")

            available_days = await get_available_days(page)

            if available_days:
                new_available_days = check_new_available_days(available_days, branch, month)
                branch_result['months'][month + 1] = available_days

                print(f"Available days: {available_days}")
                if new_available_days:
                    print(f"NEW days found: {new_available_days}")
            else:
                print("No available days in this month")

            # Go to next month (except for the last iteration)
            if month < 3:
                await open_next_month(page)

        print(f"\n✓ Branch {branch + 1} completed successfully")
        return branch_result

    except Exception as e:
        print(f"\n✗ Branch {branch + 1} failed with error: {e}")
        import traceback
        traceback.print_exc()
        return branch_result


async def run_search():
    """Main search function optimized for cloud environments"""
    print("Starting Playwright browser...")
    print(f"Environment: {'Cloud/Railway' if os.environ.get('RAILWAY_ENVIRONMENT') else 'Local'}")

    async with async_playwright() as playwright:
        # Use Firefox as alternative if Chrome fails
        browser_type = playwright.chromium

        print("Launching browser...")
        browser = await browser_type.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-gpu',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
                '--window-size=1920,1080',
                '--no-zygote',
                '--single-process',
                '--disable-accelerated-2d-canvas',
                '--disable-webgl',
                '--disable-webgl2',
            ]
        )

        print("Browser launched successfully")

        final_results = {}

        # Process branches sequentially for cloud reliability
        for branch in range(5):
            try:
                print(f"\nCreating context for branch {branch + 1}...")

                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    locale='et-EE',
                    timezone_id='Europe/Tallinn',
                    ignore_https_errors=True,
                    extra_http_headers={
                        'Accept-Language': 'et-EE,et;q=0.9,en;q=0.8',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    }
                )

                page = await context.new_page()

                # Anti-detection script
                await page.add_init_script("""
                    // Remove webdriver
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });

                    // Add plugins
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });

                    // Fix languages
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['et-EE', 'et', 'en-US', 'en']
                    });

                    // Override permissions
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications' ?
                            Promise.resolve({ state: Notification.permission }) :
                            originalQuery(parameters)
                    );
                """)

                # Enable console messages for debugging
                page.on("console", lambda msg: print(f"Console: {msg.text}"))
                page.on("pageerror", lambda msg: print(f"Page error: {msg}"))

                # Search the branch
                result = await search_branch(page, branch)

                if result and result.get('months'):
                    final_results[f"Branch {result['branch']}"] = result['months']

                # Clean up
                await context.close()

                # Delay between branches
                if branch < 4:
                    print(f"\nWaiting before next branch...")
                    await asyncio.sleep(5)

            except Exception as e:
                print(f"Critical error processing branch {branch + 1}: {e}")
                import traceback
                traceback.print_exc()
                continue

        await browser.close()
        print("\nBrowser closed")
        return final_results


if __name__ == '__main__':
    try:
        result = asyncio.run(run_search())
        if result:
            print("\n" + "=" * 60)
            print("FINAL RESULTS")
            print("=" * 60)
            for branch, months in result.items():
                print(f"\n{branch}:")
                for month, days in months.items():
                    print(f"  Month {month}: {sorted([int(d) for d in days])}")
        else:
            print("\n" + "=" * 60)
            print("NO RESULTS")
            print("=" * 60)
            print("No available days found in any branch")
    except KeyboardInterrupt:
        print("\nScript interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\nFatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)