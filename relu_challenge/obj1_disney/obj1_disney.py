"""
Objective 1: Disney Cruise Line Scraper
Extracts all cruise data from disneycruise.disney.go.com
Uses Playwright for browser automation and intercepts API responses for structured data.
"""

import json
import csv
import re
import sys
import io
from datetime import datetime
from playwright.sync_api import sync_playwright

# Fix encoding for special characters like ₹ in terminal output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Site URL and API endpoint that returns cruise product data as JSON
BASE_URL = "https://disneycruise.disney.go.com/en-in/"
API_ENDPOINT = "dcl-apps-productavail-vas/available-products"
OUTPUT_CSV = "disney_cruise_results.csv"
TEMP_JSON = "disney_cruise_raw.json"


def extract_departure_port(product_name):
    """Extract departure port from product name like '3-Night Cruise from Singapore'."""
    match = re.search(r"from\s+(.+)$", product_name, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def extract_duration(product_name):
    """Extract number of nights from product name like '3-Night Cruise'."""
    match = re.search(r"(\d+)-Night", product_name, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def get_stateroom_price(travel_parties, stateroom_type_key):
    """Get the total price for a specific stateroom type (INSIDE, OUTSIDE, VERANDAH, SUITE)."""
    # "0" is the default travel party (2 adults)
    if "0" not in travel_parties:
        return ""
    for party in travel_parties["0"]:
        if stateroom_type_key.lower() in party.get("stateroomType", "").lower():
            price_info = party.get("price", {}).get("summary", {})
            total = price_info.get("total", "")
            return f"${total}" if total else ""
    return ""


def parse_sailing(sailing, product_name, product_display_name, ports_of_call, card_tags):
    """Parse a single sailing into a flat row dict with all required fields."""
    ship = sailing.get("ship", {})
    ship_name = ship.get("name", "")
    num_nights = sailing.get("numberOfNights", extract_duration(product_name))
    destination = sailing.get("destination", "")
    geo_area = sailing.get("geoArea", "")
    departure_port = extract_departure_port(product_name)
    sailing_id = sailing.get("sailingId", "")
    package_code = sailing.get("packageCode", "")

    # Extract prices for all 4 stateroom types
    travel_parties = sailing.get("travelParties", {})
    interior_price = get_stateroom_price(travel_parties, "INSIDE")
    oceanview_price = get_stateroom_price(travel_parties, "OUTSIDE")
    verandah_price = get_stateroom_price(travel_parties, "VERANDAH")
    suite_price = get_stateroom_price(travel_parties, "SUITE")

    return {
        "Title": product_display_name,
        "Ship": ship_name,
        "Departing From": departure_port,
        "Duration": f"{num_nights} Nights" if num_nights else "",
        "Destination": destination,
        "Geo Area": geo_area,
        "Itinerary (Ports of Call)": ", ".join(ports_of_call) if ports_of_call else "",
        "Sailing ID": sailing_id,
        "Package Code": package_code,
        "Interior Price": interior_price,
        "Oceanview Price": oceanview_price,
        "Verandah Price": verandah_price,
        "Suite Price": suite_price,
        "Number of Dates": "",
        "Available": sailing.get("available", ""),
        "Tags": ", ".join(card_tags) if card_tags else "",
    }


def parse_product(product, card_info=None):
    """Parse a product JSON object into a list of row dicts (one per sailing)."""
    product_name = product.get("productName", "")
    # productDisplayName is the clean name shown on cards (without holiday prefix)
    product_display_name = product.get("productDisplayName", product_name)
    rows = []

    for itinerary in product.get("itineraries", []):
        # Extract port names from itinerary (can be string or dict)
        ports_of_call_raw = itinerary.get("portsOfCall", [])
        ports_of_call = []
        for p in ports_of_call_raw:
            if isinstance(p, dict):
                ports_of_call.append(p.get("name", str(p)))
            else:
                ports_of_call.append(str(p))

        num_sailings = itinerary.get("numberOfSailings", 0)

        # Extract holiday/themed tags from the API data
        # Tags like "Very Merrytime" and "Halloween on the High Seas" are stored
        # inside productItineraryData.themeData.bannerData.bannerHeader
        card_tags = []
        pid = product.get("productItineraryData", {})
        theme_data = pid.get("themeData", {}) if isinstance(pid, dict) else {}
        banner = theme_data.get("bannerData", {}).get("bannerHeader", "")

        # Fallback: if bannerHeader is missing, check the productName for known keywords
        if not banner:
            for kw in ["Very Merrytime", "Halloween on the High Seas",
                        "Marvel Days at Sea", "Pixar Days at Sea"]:
                if kw.lower() in product_name.lower():
                    banner = kw
                    break
        if banner:
            card_tags.append(banner)

        # Merge any additional tags from the rendered page cards
        if card_info:
            for t in card_info.get("tags", []):
                if t not in card_tags:
                    card_tags.append(t)
            if not ports_of_call and card_info.get("sailing_to"):
                ports_of_call = [card_info["sailing_to"]]

        # Each itinerary can have multiple sailings (different dates)
        for sailing in itinerary.get("sailings", []):
            row = parse_sailing(
                sailing, product_name, product_display_name,
                ports_of_call, card_tags
            )
            row["Number of Dates"] = num_sailings
            rows.append(row)

    return rows


def extract_card_info_from_page(page):
    """Extract tag/badge information from all visible cruise cards on the rendered page.
    This captures badges like 'Very Merrytime' that are rendered by the Angular frontend."""
    card_data = page.evaluate("""() => {
        const cards = document.querySelectorAll('div.product-card-wrapper');
        const results = [];
        cards.forEach(card => {
            const nameEl = card.querySelector('.product-card-content__name');
            const name = nameEl ? nameEl.textContent.trim() : '';

            const sailEl = card.querySelector('.product-card-content__sailing-to');
            let sailingTo = sailEl ? sailEl.textContent.trim() : '';
            sailingTo = sailingTo.replace('Sailing to', '').trim();

            // Look for badge/tag text using multiple CSS selectors
            const tags = [];
            const badgeSelectors = [
                '.product-card-badge__text',
                '.product-card-image__badge span',
                '.chip-label',
                '.product-card-badge',
                '[class*="badge"]',
                '[class*="theme"]',
                '[class*="event"]'
            ];
            badgeSelectors.forEach(sel => {
                card.querySelectorAll(sel).forEach(el => {
                    const txt = el.textContent.trim();
                    if (txt && txt.length < 60 && !txt.includes('Guaranteed') && !txt.includes('Price')) {
                        if (!tags.includes(txt)) tags.push(txt);
                    }
                });
            });

            // Check image overlay badges (holiday themed images)
            const imgBadges = card.querySelectorAll('.product-card-image-mobile-desktop-wrapper [class*="badge"], .product-card__image [class*="badge"]');
            imgBadges.forEach(el => {
                const txt = el.textContent.trim();
                if (txt && txt.length < 60 && !tags.includes(txt)) tags.push(txt);
            });

            results.push({ name, sailingTo, tags });
        });
        return results;
    }""")
    return card_data


def scrape_disney_cruises():
    """Main scraper function.
    1. Opens the Disney Cruise site in a browser
    2. Clicks 'View Dates' to load the cruise listing
    3. Scrolls down to trigger infinite scroll (loads new API pages)
    4. Intercepts all API responses to capture structured JSON data
    """
    all_api_responses = []

    def on_response(resp):
        """Callback: intercepts every HTTP response and captures the product API calls."""
        url = resp.url
        if API_ENDPOINT in url:
            try:
                body = resp.json()
                page_num = len(all_api_responses) + 1
                all_api_responses.append(body)
                products = body.get("products", [])
                total_pages = body.get("totalPages", 0)
                total_cruises = body.get("totalAvailableCruises", 0)
                print(f"  [API] Page {page_num}/{total_pages} captured - "
                      f"{len(products)} products (Total: {total_cruises})")
            except Exception as e:
                print(f"  [API] Error parsing response: {e}")

    with sync_playwright() as p:
        # Launch browser with visible window (headless=False) for reliable page rendering
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # Register the response interceptor before navigating
        page.on("response", on_response)

        # Step 1: Navigate to the Disney Cruise site
        print("Step 1: Navigating to Disney Cruise site...")
        page.goto(BASE_URL, timeout=60000)
        page.wait_for_timeout(8000)

        # Try to dismiss cookie/consent popup if it appears
        for label in ["Agree", "Accept", "I Agree", "Accept All", "Accept Cookies"]:
            try:
                page.get_by_role("button", name=label).click(timeout=2000)
                print(f"  Dismissed consent popup: '{label}'")
                break
            except Exception:
                pass

        # Step 2: Click View Dates button to load cruise listings
        print("Step 2: Clicking 'View Dates'...")
        try:
            page.locator("button.view-cruises-button").click(timeout=15000)
        except Exception:
            # Fallback: find by text if CSS selector doesn't match
            page.get_by_text("View Dates").first.click(timeout=15000)
        page.wait_for_timeout(10000)

        # Step 3: Wait for initial results to load
        print("Step 3: Waiting for cruise results to load...")
        page.wait_for_timeout(5000)

        # Step 4: Scroll down repeatedly to trigger infinite scroll
        # The site loads 5 products per API page, so we need 35+ pages for all cruises
        print("Step 4: Scrolling to load all cruise cards...")
        target_pages = 35
        max_scroll_attempts = 200
        scroll_count = 0
        last_api_count = 0
        stale_count = 0

        while len(all_api_responses) < target_pages and scroll_count < max_scroll_attempts:
            # Scroll down 1500px to trigger the next page load
            page.evaluate("window.scrollBy(0, 1500)")
            page.wait_for_timeout(1500)
            scroll_count += 1

            # Track if new API pages were loaded
            if len(all_api_responses) > last_api_count:
                last_api_count = len(all_api_responses)
                stale_count = 0
            else:
                stale_count += 1

            # If no new pages loaded after 10 scrolls, jump to bottom to force load
            if stale_count > 10:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(3000)
                stale_count = 0

            if scroll_count % 20 == 0:
                print(f"  Scrolled {scroll_count} times, "
                      f"captured {len(all_api_responses)} API pages...")

        print(f"\nTotal API pages captured: {len(all_api_responses)}")

        # Also extract badge/tag info from the rendered DOM as a secondary source
        print("Extracting card tags from rendered page...")
        card_infos = extract_card_info_from_page(page)
        print(f"  Extracted info from {len(card_infos)} visible cards")

        # Log unique tags found on the page
        all_tags_found = set()
        for ci in card_infos:
            for t in ci.get("tags", []):
                all_tags_found.add(t)
        if all_tags_found:
            print(f"  Tags found: {all_tags_found}")

        browser.close()

    return all_api_responses, card_infos


def process_and_save(api_responses, card_infos):
    """Process all captured API responses, parse into rows, clean, and save to CSV."""
    # Save raw JSON for debugging and reprocessing
    print(f"\nSaving raw data to {TEMP_JSON}...")
    raw_products = []
    for resp in api_responses:
        for prod in resp.get("products", []):
            raw_products.append(prod)

    with open(TEMP_JSON, "w", encoding="utf-8") as f:
        json.dump(raw_products, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(raw_products)} raw products")

    # Build a lookup table to match card tags to products by display name
    card_lookup = {}
    for ci in card_infos:
        if ci.get("name"):
            card_lookup[ci["name"]] = ci

    # Parse all products into flat rows (one row per sailing date)
    all_rows = []
    seen = set()

    for product in raw_products:
        card_info = card_lookup.get(product.get("productDisplayName"), None)
        rows = parse_product(product, card_info)
        for row in rows:
            # Deduplicate using a composite key of title + ship + sailing ID
            key = (row["Title"], row["Ship"], row["Sailing ID"])
            if key not in seen:
                seen.add(key)
                all_rows.append(row)

    print(f"  Total unique sailings: {len(all_rows)}")

    # Step 5: Data Cleaning
    print("\nStep 5: Applying data cleaning rules...")
    cleaned_rows = []
    for row in all_rows:
        # Skip rows missing essential fields
        if not row["Title"] or not row["Ship"]:
            continue

        # Try to extract departure port from title if missing
        if not row["Departing From"]:
            match = re.search(r"from\s+(.+)$", row["Title"], re.IGNORECASE)
            if match:
                row["Departing From"] = match.group(1).strip()

        # Normalize whitespace and fill empty fields with N/A
        for k, v in row.items():
            if isinstance(v, str):
                row[k] = " ".join(v.split())
                if not row[k].strip():
                    row[k] = "N/A"
        cleaned_rows.append(row)

    print(f"  Cleaned rows: {len(cleaned_rows)}")

    # Step 6: Save final cleaned data to CSV
    print(f"\nStep 6: Saving to {OUTPUT_CSV}...")
    fieldnames = [
        "Title", "Ship", "Departing From", "Duration", "Destination",
        "Geo Area", "Itinerary (Ports of Call)", "Sailing ID", "Package Code",
        "Interior Price", "Oceanview Price", "Verandah Price", "Suite Price",
        "Number of Dates", "Available", "Tags"
    ]

    # utf-8-sig adds BOM for proper Excel compatibility
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(cleaned_rows)

    print(f"  Saved {len(cleaned_rows)} rows to {OUTPUT_CSV}")
    return cleaned_rows


def answer_questions(rows):
    """Answer the 5 submission questions from the extracted data."""
    print("\n" + "=" * 60)
    print("ANSWERS TO SUBMISSION QUESTIONS")
    print("=" * 60)

    # (i) Pacific cruises: match by destination or title keywords
    pacific_keywords = ["pacific", "california coast", "mexican riviera",
                        "baja", "alaska", "vancouver", "san diego"]
    pacific_cruises = [
        r for r in rows
        if any(kw in r.get("Destination", "").lower()
               or kw in r.get("Title", "").lower()
               for kw in pacific_keywords)
    ]
    print(f"\n(i) Total cruises for the Pacific as a destination: {len(pacific_cruises)}")

    # (ii) Total number of cruises
    print(f"(ii) Total cruises: {len(rows)}")

    # (iii) Holiday cruises: identified by tags like "Very Merrytime" or "Halloween"
    holiday_keywords = ["holiday", "halloween", "christmas", "thanksgiving",
                        "new year", "very merrytime", "very merry", "festive"]
    holiday_cruises = [
        r for r in rows
        if any(kw in r.get("Tags", "").lower() or kw in r.get("Title", "").lower()
               for kw in holiday_keywords)
    ]
    print(f"(iii) Total holiday cruises: {len(holiday_cruises)}")

    # (iv) Cruises offering more than 2 booking dates
    # Number of Dates = how many sailing dates each product/itinerary offers
    product_date_counts = {}
    for r in rows:
        title = r["Title"]
        num_dates = r.get("Number of Dates", 0)
        try:
            num_dates = int(num_dates)
        except (ValueError, TypeError):
            num_dates = 0
        product_date_counts[title] = num_dates

    more_than_2 = sum(1 for count in product_date_counts.values() if count > 2)
    print(f"(iv) Cruises offering more than 2 dates for booking: {more_than_2}")

    # (v) Count cruises departing from Miami and London
    # Fort Lauderdale is the actual port for Miami area cruises
    # Southampton is the actual port for London area cruises
    miami_cruises = [r for r in rows if "miami" in r.get("Departing From", "").lower()
                     or "fort lauderdale" in r.get("Departing From", "").lower()]
    london_cruises = [r for r in rows if "london" in r.get("Departing From", "").lower()
                      or "southampton" in r.get("Departing From", "").lower()]
    print(f"(v) Cruises from Miami (incl. Fort Lauderdale): {len(miami_cruises)}, "
          f"Cruises from London (incl. Southampton): {len(london_cruises)}")

    print("=" * 60)


def reprocess_from_json():
    """Reprocess from saved raw JSON without re-scraping.
    Useful for regenerating CSV after code changes without re-running the browser."""
    print("Reprocessing from saved raw JSON...")
    with open(TEMP_JSON, "r", encoding="utf-8") as f:
        raw_products = json.load(f)
    print(f"  Loaded {len(raw_products)} products from {TEMP_JSON}")

    api_responses = [{"products": raw_products}]
    return process_and_save(api_responses, [])


if __name__ == "__main__":
    print("Disney Cruise Line Data Scraper")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 60)

    # Use --reprocess flag to regenerate CSV from saved JSON without re-scraping
    if "--reprocess" in sys.argv:
        rows = reprocess_from_json()
    else:
        api_responses, card_infos = scrape_disney_cruises()
        rows = process_and_save(api_responses, card_infos)
    answer_questions(rows)

    print(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
