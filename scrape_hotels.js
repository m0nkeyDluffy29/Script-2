const fs = require("fs/promises");
const { chromium, firefox } = require("playwright");

const START_URL = "https://www.adanione.com/hotels/srp?city=Udaipur";
const LISTING_BUTTON_SELECTOR =
  "p.btn.xsmall.btn-001.round.outline.flx.mr-t28.anim.pd-rl16";
const HOTEL_NAME_SELECTOR = "a.flx.cp.full.card-box.hotel-card.mr-b20.anim";
const IMAGE_SELECTOR = ".img-container-web img.img-card";
const OUTPUT_FILE = "hotel_images_by_name.json";

const BROWSER_CANDIDATES = [
  {
    name: "chromium",
    launcher: chromium,
    launchOptions: {
      headless: true,
      args: ["--disable-http2"],
    },
  },
  {
    name: "firefox",
    launcher: firefox,
    launchOptions: {
      headless: true,
    },
  },
];

async function navigateWithRetry(page, url, timeout = 60000) {
  const waitStates = ["domcontentloaded", "load", "commit"];
  let lastError;

  for (let i = 0; i < waitStates.length; i += 1) {
    try {
      await page.goto(url, {
        waitUntil: waitStates[i],
        timeout,
      });
      return;
    } catch (error) {
      lastError = error;
      const isHttp2Error = String(error && error.message).includes(
        "ERR_HTTP2_PROTOCOL_ERROR",
      );

      if (!isHttp2Error && i === waitStates.length - 1) {
        throw error;
      }

      await page.waitForTimeout(1200 * (i + 1));
    }
  }

  throw lastError;
}

async function autoScrollToLoadAll(page, maxScrolls = 20) {
  let lastCount = 0;
  let stableRounds = 0;

  for (let i = 0; i < maxScrolls; i += 1) {
    const count = await page.locator(LISTING_BUTTON_SELECTOR).count();

    if (count === lastCount) {
      stableRounds += 1;
    } else {
      stableRounds = 0;
      lastCount = count;
    }

    await page.mouse.wheel(0, 2400);
    await page.waitForTimeout(800);

    if (stableRounds >= 2) {
      break;
    }
  }

  return page.locator(LISTING_BUTTON_SELECTOR).count();
}

/**
 * Waits for either a URL change away from `fromUrl` OR the image selector to
 * appear. Covers both full SPA route changes and modal-based detail views.
 */
async function waitForDetailContent(page, fromUrl, timeout = 12000) {
  const deadline = Date.now() + timeout;

  while (Date.now() < deadline) {
    const currentUrl = page.url();
    const urlChanged = currentUrl !== fromUrl && !currentUrl.includes("/srp?");

    const imagesVisible = await page
      .locator(IMAGE_SELECTOR)
      .first()
      .isVisible()
      .catch(() => false);

    if (urlChanged || imagesVisible) {
      return { urlChanged, imagesVisible };
    }

    await page.waitForTimeout(300);
  }

  return { urlChanged: false, imagesVisible: false };
}

/**
 * Tries to close a detail modal / navigate back to the SRP listing.
 */
async function returnToListing(page, originalUrl) {
  const currentUrl = page.url();

  if (currentUrl !== originalUrl) {
    await page
      .goBack({ waitUntil: "domcontentloaded", timeout: 20000 })
      .catch(() => {});
    await navigateWithRetry(page, originalUrl, 30000).catch(() => {});
  } else {
    // Modal scenario — try common close patterns
    const closeSelectors = [
      "button[aria-label*='close' i]",
      "button[aria-label*='back' i]",
      ".modal-close",
      ".close-btn",
      "[class*='close']",
    ];
    for (const sel of closeSelectors) {
      const btn = page.locator(sel).first();
      if (await btn.isVisible().catch(() => false)) {
        await btn.click().catch(() => {});
        await page.waitForTimeout(600);
        break;
      }
    }
    // Press Escape as final fallback
    await page.keyboard.press("Escape").catch(() => {});
    await page.waitForTimeout(600);
  }

  await page
    .waitForSelector(LISTING_BUTTON_SELECTOR, { timeout: 15000 })
    .catch(() => {});
}

async function scrapeAllHotels(page) {
  await navigateWithRetry(page, START_URL, 60000);
  await page.waitForSelector(LISTING_BUTTON_SELECTOR, { timeout: 30000 });
  await autoScrollToLoadAll(page);

  // Snapshot hotel names and buttons from the listing page before any click
  const hotelCards = await page.$$eval(
    LISTING_BUTTON_SELECTOR,
    (buttons, nameSelector) => {
      return buttons.map((btn, idx) => {
        // Walk up to the hotel card container, then find the name sibling
        let card = btn;
        for (let depth = 0; depth < 8; depth += 1) {
          if (!card.parentElement) break;
          card = card.parentElement;
          const nameEl = card.querySelector(nameSelector);
          if (nameEl) {
            return { index: idx, name: (nameEl.textContent || "").trim() };
          }
        }
        return { index: idx, name: null };
      });
    },
    HOTEL_NAME_SELECTOR,
  );

  console.log(`Detected ${hotelCards.length} hotel cards on listing page.`);

  const groupedByHotelName = {};

  for (let i = 0; i < hotelCards.length; i += 1) {
    const card = hotelCards[i];
    const listingUrl = page.url();

    console.log(`Processing hotel ${i + 1}/${hotelCards.length}`);

    const button = page.locator(LISTING_BUTTON_SELECTOR).nth(i);
    await button.scrollIntoViewIfNeeded().catch(() => {});
    await page.waitForTimeout(400);

    await button.click({ timeout: 10000 }).catch(() => {});

    const { urlChanged, imagesVisible } = await waitForDetailContent(
      page,
      listingUrl,
    );

    if (!urlChanged && !imagesVisible) {
      console.warn(`  No detail content loaded for card ${i + 1}, skipping.`);
      await returnToListing(page, listingUrl);
      continue;
    }

    await page.waitForTimeout(800);

    // Prefer the hotel name found in-place on the detail view
    const hotelName =
      (await page
        .locator(HOTEL_NAME_SELECTOR)
        .first()
        .textContent()
        .then((t) => (t || "").trim())
        .catch(() => null)) ||
      card.name ||
      `Hotel_${i + 1}`;

    const imageUrls = await page.$$eval(IMAGE_SELECTOR, (images) =>
      images
        .map((img) => img.getAttribute("src") || img.getAttribute("data-src"))
        .filter(Boolean)
        .map((src) => {
          try {
            return new URL(src, document.baseURI).href;
          } catch {
            return src;
          }
        }),
    );

    const uniqueImages = [...new Set(imageUrls)];
    console.log(`  "${hotelName}" — ${uniqueImages.length} image(s)`);

    if (!groupedByHotelName[hotelName]) {
      groupedByHotelName[hotelName] = [];
    }
    groupedByHotelName[hotelName].push(...uniqueImages);
    groupedByHotelName[hotelName] = [...new Set(groupedByHotelName[hotelName])];

    await returnToListing(page, listingUrl);
  }

  return groupedByHotelName;
}

async function runScrapeWithBrowser(candidate) {
  const browser = await candidate.launcher.launch(candidate.launchOptions);
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    userAgent:
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  });
  const page = await context.newPage();

  try {
    console.log(`Using browser: ${candidate.name}`);
    const groupedByHotelName = await scrapeAllHotels(page);
    const totalHotels = Object.keys(groupedByHotelName).length;

    if (totalHotels === 0) {
      throw new Error(
        "No hotel data collected — check selectors or page structure.",
      );
    }

    await fs.writeFile(
      OUTPUT_FILE,
      JSON.stringify(groupedByHotelName, null, 2),
      "utf8",
    );

    console.log(
      `Saved ${totalHotels} hotel(s) with their images to ${OUTPUT_FILE}`,
    );
    return;
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
}

async function main() {
  let lastError;

  for (const candidate of BROWSER_CANDIDATES) {
    try {
      await runScrapeWithBrowser(candidate);
      return;
    } catch (error) {
      lastError = error;
      console.warn(
        `Attempt failed with ${candidate.name}: ${error && error.message ? error.message : error}`,
      );
    }
  }

  throw lastError;
}

main().catch((error) => {
  console.error("Scrape failed:", error);
  process.exitCode = 1;
});
