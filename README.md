# AdaniOne Hotel Image Scraper

This project provides a Python script to scrape hotel names and their associated images from the AdaniOne Udaipur hotel listings page. The script uses Playwright for browser automation and outputs a JSON file mapping hotel names to lists of image URLs.

## Features

- Scrapes hotel names and detail page links from the AdaniOne Udaipur hotels listing.
- Visits each hotel detail page to collect all available image URLs.
- Outputs results as a JSON file, grouping images by hotel name.
- Handles dynamic loading and pagination ("Show More" button).
- Supports Chromium and Firefox browsers in headless mode.

## Requirements

- Python 3.8+
- Playwright (Python)
- Node.js (for Playwright installation)

## Installation

1. **Install Python dependencies:**
   ```bash
   pip install playwright
   playwright install
   ```
2. **(Optional) Create a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

## Usage

Run the scraper with the default AdaniOne Udaipur hotels URL:

```bash
python scrape.py
```

Or specify a different URL and output file:

```bash
python scrape.py "https://www.adanione.com/hotels/srp?city=Udaipur" --json-out my_hotels.json
```

### Optional Arguments

- `url` — The starting URL to scrape (default: Udaipur hotels).
- `--json-out` — Output JSON file (default: hotel_images_by_name.json).
- `--max-hotels` — Maximum number of hotels to process (default: all).

## Output

The script generates a JSON file mapping hotel names to lists of image URLs, e.g.:

```json
{
  "Hotel XYZ": [
    "https://.../image1.jpg",
    "https://.../image2.jpg"
  ],
  ...
}
```

## Notes

- The script is tailored for the AdaniOne hotel listing structure and may require adjustments if the website layout changes.
- For large scrapes, ensure a stable internet connection and sufficient system resources.

## License

MIT
