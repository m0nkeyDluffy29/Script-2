# Image Scraper

This project provides a Python script to scrape YOUR_WEBSITE names and their associated images from the  listings page. The script uses Playwright for browser automation and outputs a JSON file mapping hotel names to lists of image URLs.

## Features

- Scrapes hotel names and detail page links from the YOUR_WEBSITE.
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

Run the scraper with the default YOUR_WEBSITE URL:

```bash
python scrape.py
```

Or specify a different URL and output file:

```bash
python scrape.py "https://www.YOUR_WEBSITE.com/" --json-out my_FILENAME.json
```

### Optional Arguments

- `url` — The starting URL to scrape (default: YOUR_WEBSITE_NAME).
- `--json-out` — Output JSON file (default: FILE_NAME.json).
- `--max-value` — Maximum number to process (default: all).

## Output

The script generates a JSON file mapping names to lists of image URLs, e.g.:

```json
{
  "XYZ": [
    "https://.../image1.jpg",
    "https://.../image2.jpg"
  ],
  ...
}
```

## Notes

- The script is tailored for the YOUR_WEBSITE listing structure and may require adjustments if the website layout changes.
- For large scrapes, ensure a stable internet connection and sufficient system resources.

## License

MIT
