import argparse
import asyncio
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


START_URL = "https://www.adanione.com/hotels/srp?city=Udaipur"
HOTEL_CARD_SELECTOR = "a.flx.cp.full.card-box.hotel-card.mr-b20.anim"
IMAGE_SELECTOR = ".img-container-web img.img-card"
SHOW_MORE_SELECTOR = "span.show-more.btn.anim.round.btn-004"
SHOW_MORE_BATCH_SIZE = 50
DEFAULT_TIMEOUT_MS = 30000
OUTPUT_JSON = "hotel_images_by_name.json"
DETAIL_FETCH_RETRIES = 3
BROWSER_CANDIDATES = [
    {
        "name": "chromium",
        "launcher_attr": "chromium",
        "launch_options": {
            "headless": True,
            "args": ["--disable-http2"],
        },
    },
    {
        "name": "firefox",
        "launcher_attr": "firefox",
        "launch_options": {
            "headless": True,
        },
    },
]


def is_valid_url(value):
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def navigate_with_retry(page, url, retries=3, timeout=DEFAULT_TIMEOUT_MS):
    wait_states = ["domcontentloaded", "load", "commit"]
    last_error = None

    for attempt in range(retries):
        wait_until = wait_states[min(attempt, len(wait_states) - 1)]
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            return
        except (PlaywrightTimeoutError, PlaywrightError) as error:
            last_error = error
            if "ERR_HTTP2_PROTOCOL_ERROR" not in str(error) and attempt == retries - 1:
                break
            await page.wait_for_timeout(1000 * (attempt + 1))

    raise last_error


async def auto_scroll_to_load_all(page, max_rounds=12):
    previous_count = -1
    stable_rounds = 0

    for _ in range(max_rounds):
        count = await page.locator(HOTEL_CARD_SELECTOR).count()
        if count == previous_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_count = count

        await page.mouse.wheel(0, 2400)
        await page.wait_for_timeout(900)

        if stable_rounds >= 2:
            break

    return await page.locator(HOTEL_CARD_SELECTOR).count()


async def snapshot_hotel_cards(page):
    return await page.locator(HOTEL_CARD_SELECTOR).evaluate_all(
        """
        (elements) => elements.map((element, index) => ({
          index,
          name: (element.textContent || '').trim() || `Hotel_${index + 1}`,
          href: element.href || null,
        }))
        """
    )


def dedupe_preserve_order(values):
    deduped = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def hotel_identity(card):
    href = (card.get("href") or "").strip().lower()
    if href:
        return f"href:{href}"
    fallback_name = (card.get("name") or "").strip().lower()
    return f"name:{fallback_name}"


async def click_show_more(page, previous_count, timeout_ms=15000):
    button = page.locator(SHOW_MORE_SELECTOR).first

    # Some pages render/show this control only after scrolling to the listing bottom.
    # Try a short scroll probe first so we do not stop early at exactly 50 items.
    found_button = False
    for _ in range(10):
        try:
            if await button.is_visible(timeout=500):
                found_button = True
                break
        except PlaywrightError:
            pass

        await page.mouse.wheel(0, 2800)
        await page.wait_for_timeout(350)

    if not found_button:
        return False

    try:
        await button.scroll_into_view_if_needed(timeout=5000)
    except PlaywrightError:
        pass

    await page.wait_for_timeout(250)
    try:
        await button.click(timeout=7000)
    except PlaywrightError:
        # Some sites attach click handlers to parent nodes; this is a safe fallback.
        await page.evaluate(
            "(selector) => document.querySelector(selector)?.click()",
            SHOW_MORE_SELECTOR,
        )

    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
    while asyncio.get_running_loop().time() < deadline:
        current_count = await page.locator(HOTEL_CARD_SELECTOR).count()
        if current_count > previous_count:
            return True

        try:
            still_visible = await page.locator(SHOW_MORE_SELECTOR).first.is_visible(
                timeout=250
            )
        except PlaywrightError:
            still_visible = False

        if not still_visible and current_count <= previous_count:
            return False

        await page.wait_for_timeout(350)

    return await page.locator(HOTEL_CARD_SELECTOR).count() > previous_count


async def wait_for_detail_content(page, listing_url, timeout_ms=12000):
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)

    while asyncio.get_running_loop().time() < deadline:
        if page.url != listing_url:
            return True

        try:
            if await page.locator(IMAGE_SELECTOR).first.is_visible(timeout=500):
                return True
        except PlaywrightError:
            pass

        await page.wait_for_timeout(300)

    return False


async def collect_image_urls(page):
    images = await page.locator(IMAGE_SELECTOR).evaluate_all(
        """
        (elements) => elements
          .map((element) => element.getAttribute('src') || element.getAttribute('data-src'))
          .filter(Boolean)
          .map((value) => {
            try {
              return new URL(value, document.baseURI).href;
            } catch (error) {
              return value;
            }
          })
        """
    )

    return dedupe_preserve_order(images)


async def return_to_listing(page, listing_url):
    if page.url != listing_url:
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=15000)
        except PlaywrightError:
            await navigate_with_retry(page, listing_url, retries=2, timeout=20000)
    else:
        close_selectors = [
            "button[aria-label*='close' i]",
            "button[aria-label*='back' i]",
            ".modal-close",
            ".close-btn",
            "[class*='close']",
        ]
        for selector in close_selectors:
            button = page.locator(selector).first
            try:
                if await button.is_visible(timeout=1000):
                    await button.click(timeout=3000)
                    break
            except PlaywrightError:
                continue

        try:
            await page.keyboard.press("Escape")
        except PlaywrightError:
            pass

    await page.wait_for_timeout(800)
    await page.wait_for_selector(HOTEL_CARD_SELECTOR, timeout=DEFAULT_TIMEOUT_MS)


async def collect_hotel_images(context, href, retries=DETAIL_FETCH_RETRIES):
    last_error = None

    for attempt in range(retries):
        detail_page = await context.new_page()
        detail_page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        try:
            await navigate_with_retry(detail_page, href, retries=4, timeout=45000)
            await detail_page.wait_for_selector(IMAGE_SELECTOR, timeout=DEFAULT_TIMEOUT_MS)
            await detail_page.wait_for_timeout(1200)
            return await collect_image_urls(detail_page)
        except (PlaywrightTimeoutError, PlaywrightError) as error:
            last_error = error
            await detail_page.close()
            await asyncio.sleep(1.2 * (attempt + 1))
            continue
        finally:
            if not detail_page.is_closed():
                await detail_page.close()

    raise last_error


async def collect_hotel_images_from_click(page, index, retries=DETAIL_FETCH_RETRIES):
    last_error = None

    for attempt in range(retries):
        popup = None
        locator = page.locator(HOTEL_CARD_SELECTOR).nth(index)

        try:
            await locator.scroll_into_view_if_needed(timeout=5000)
            await page.wait_for_timeout(400)
            async with page.expect_popup(timeout=10000) as popup_info:
                await locator.click(timeout=10000)
            popup = await popup_info.value
            popup.set_default_timeout(DEFAULT_TIMEOUT_MS)
            await popup.wait_for_load_state(
                "domcontentloaded", timeout=DEFAULT_TIMEOUT_MS
            )
            await popup.wait_for_selector(IMAGE_SELECTOR, timeout=80000)
            await popup.wait_for_timeout(1200)
            return await collect_image_urls(popup)
        except (PlaywrightTimeoutError, PlaywrightError) as error:
            last_error = error
            await page.wait_for_timeout(800 * (attempt + 1))
        finally:
            if popup is not None:
                await popup.close()

    raise last_error


async def scrape_hotels(url, max_hotels=None):
    async with async_playwright() as playwright:
        last_error = None

        for candidate in BROWSER_CANDIDATES:
            browser = None
            try:
                launcher = getattr(playwright, candidate["launcher_attr"])
                browser = await launcher.launch(**candidate["launch_options"])
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 2200}
                )
                page = await context.new_page()
                page.set_default_timeout(DEFAULT_TIMEOUT_MS)

                print(f"Using browser: {candidate['name']}")
                await navigate_with_retry(page, url)
                await page.wait_for_selector(
                    HOTEL_CARD_SELECTOR, timeout=DEFAULT_TIMEOUT_MS
                )
                initial_cards = await auto_scroll_to_load_all(page)
                print(f"Detected {initial_cards} initially loaded hotel cards.")

                grouped_images = {}
                processed_identities = set()
                processed_count = 0

                cards = await snapshot_hotel_cards(page)

                while True:
                    while processed_count < len(cards):
                        if max_hotels is not None and processed_count >= max_hotels:
                            await browser.close()
                            return grouped_images

                        card = cards[processed_count]
                        hotel_name = card["name"]
                        index = card["index"]
                        href = card.get("href")

                        identity = hotel_identity(card)
                        if identity in processed_identities:
                            print(
                                f"Skipping duplicate listing at index {index + 1}: {hotel_name}"
                            )
                            processed_count += 1
                            continue

                        processed_identities.add(identity)
                        print(
                            f"Processing {processed_count + 1}/{len(cards)} loaded: {hotel_name}"
                        )

                        if not href:
                            print(f"Skipping {hotel_name}: missing hotel detail URL.")
                            processed_count += 1
                            continue

                        image_urls = []
                        try:
                            image_urls = await collect_hotel_images_from_click(page, index)
                        except PlaywrightError as error:
                            print(
                                f"Click flow failed for {hotel_name}, retrying via href ({error})"
                            )
                            try:
                                image_urls = await collect_hotel_images(context, href)
                            except PlaywrightError as fallback_error:
                                print(
                                    f"Skipping {hotel_name}: detail page failed ({fallback_error})"
                                )
                                processed_count += 1
                                continue

                        existing = grouped_images.get(hotel_name, [])
                        grouped_images[hotel_name] = dedupe_preserve_order(
                            existing + image_urls
                        )
                        print(
                            f"Collected {len(grouped_images[hotel_name])} images for {hotel_name}."
                        )
                        processed_count += 1

                        if processed_count % SHOW_MORE_BATCH_SIZE == 0:
                            previous_count = len(cards)
                            expanded = await click_show_more(page, previous_count)
                            if expanded:
                                cards = await snapshot_hotel_cards(page)
                                print(
                                    f"Loaded more hotels: {previous_count} -> {len(cards)}"
                                )

                    previous_count = len(cards)
                    expanded = await click_show_more(page, previous_count)
                    if not expanded:
                        break

                    cards = await snapshot_hotel_cards(page)
                    print(f"Loaded more hotels: {previous_count} -> {len(cards)}")

                await browser.close()
                return grouped_images
            except PlaywrightError as error:
                last_error = error
                print(f"Browser {candidate['name']} failed: {error}")
            finally:
                if browser is not None:
                    await browser.close()

        raise last_error


def write_json_output(grouped_images, destination):
    destination.write_text(json.dumps(grouped_images, indent=2), encoding="utf-8")


def sanitize_path_segment(value):
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:90] or "hotel"


def extension_from_url(url):
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ext
    return ".jpg"


def download_image_file(url, destination):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        destination.write_bytes(response.read())


async def write_image_files(grouped_images, images_root):
    images_root.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    skipped = 0
    failed = 0

    for hotel_name, image_urls in grouped_images.items():
        hotel_dir = images_root / sanitize_path_segment(hotel_name)
        hotel_dir.mkdir(parents=True, exist_ok=True)

        for image_index, image_url in enumerate(image_urls, start=1):
            file_name = f"image_{image_index:03d}{extension_from_url(image_url)}"
            file_path = hotel_dir / file_name

            if file_path.exists() and file_path.stat().st_size > 0:
                skipped += 1
                continue

            try:
                await asyncio.to_thread(download_image_file, image_url, file_path)
                downloaded += 1
            except Exception:
                failed += 1

    return {"downloaded": downloaded, "skipped": skipped, "failed": failed}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape Adani One hotel images grouped by hotel name."
    )
    parser.add_argument("url", nargs="?", default=START_URL)
    parser.add_argument("--json-out", default=OUTPUT_JSON)
    parser.add_argument(
        "--max-hotels",
        type=int,
        default=None,
        help="Maximum number of hotels to process (default: all).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if not is_valid_url(args.url):
        raise SystemExit(f"Invalid URL: {args.url}")

    results = asyncio.run(scrape_hotels(args.url, max_hotels=args.max_hotels))

    json_path = Path(args.json_out)
    write_json_output(results, json_path)

    print(f"Saved JSON output to {json_path}")
