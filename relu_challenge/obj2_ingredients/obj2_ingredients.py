"""
Objective 2: Ingredients Network Scraper
Extracts all company and ingredient data from ingredientsnetwork.com
Uses requests + BeautifulSoup for efficient HTML scraping across the alphabetical directory.
"""

import json
import csv
import re
import sys
import io
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup

# Fix encoding for special characters in terminal output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Site URLs and output file paths
BASE_URL = "https://www.ingredientsnetwork.com"
SUPPLIERS_URL = BASE_URL + "/company/{letter}.html"  # Directory pages: /company/a.html, /company/b.html, etc.
OUTPUT_CSV = "ingredients_network_results.csv"
TEMP_CSV = "ingredients_network_raw.csv"
TEMP_JSON = "ingredients_network_raw.json"

# Browser-like headers to avoid being blocked
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def decode_cf_email(encoded):
    """Decode CloudFlare email protection.
    CloudFlare hides emails using XOR encryption stored in data-cfemail attribute.
    First 2 hex chars are the key, remaining chars are XORed with the key."""
    if not encoded:
        return ""
    try:
        key = int(encoded[:2], 16)
        decoded = ""
        for i in range(2, len(encoded), 2):
            decoded += chr(int(encoded[i:i+2], 16) ^ key)
        return decoded
    except Exception:
        return ""


def get_all_company_urls():
    """Step 1-2: Collect all company profile URLs from the alphabetical directory.
    The site organizes companies by first letter: /company/a.html through /company/z.html
    plus /company/0.html for companies starting with numbers."""
    print("Step 1: Navigating to Ingredients Network...")
    print("Step 2: Collecting company URLs from supplier directory (A-Z, 0-9)...")

    all_urls = set()
    letters = list("abcdefghijklmnopqrstuvwxyz") + ["0"]

    # Use a session to reuse TCP connections for faster requests
    session = requests.Session()
    session.headers.update(HEADERS)

    for letter in letters:
        url = SUPPLIERS_URL.format(letter=letter)
        try:
            resp = session.get(url, timeout=30)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Company profile links contain "-comp" and end with ".html"
            # Example: /aak-comp248800.html
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "-comp" in href and href.endswith(".html"):
                    # Build full URL from relative or absolute href
                    if href.startswith("/"):
                        full_url = BASE_URL + href
                    elif href.startswith("http"):
                        full_url = href
                    else:
                        full_url = BASE_URL + "/" + href
                    # Only keep URLs from ingredientsnetwork.com
                    if "ingredientsnetwork.com" in full_url:
                        all_urls.add(full_url)

            print(f"  Letter {letter.upper()}: {len(all_urls)} total companies collected")
            # Polite delay between requests
            time.sleep(0.3)
        except Exception as e:
            print(f"  Error on letter {letter}: {e}")

    print(f"\nTotal unique company URLs: {len(all_urls)}")
    return sorted(all_urls)


def extract_company_data(url, session):
    """Extract all 10 required fields from a single company profile page.
    Fields: Company Name, Description, Sales Markets, Primary Business Activity,
    Categories, Events, Address, Email, Telephone, Website."""
    try:
        resp = session.get(url, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        return None

    data = {"URL": url}

    # Company Name: from the main heading
    h1 = soup.find("h1")
    data["Company Name"] = h1.get_text(strip=True) if h1 else ""

    # Company Description: first try the meta description tag
    meta_desc = soup.find("meta", attrs={"name": "description"})
    desc = meta_desc["content"].strip() if meta_desc and meta_desc.get("content") else ""

    # If meta description is too short, find the first meaningful paragraph
    if len(desc) < 20:
        for p in soup.find_all("p"):
            txt = p.get_text(strip=True)
            # Skip boilerplate text (cookies, privacy, etc.)
            if (len(txt) > 50 and
                not any(kw in txt.lower() for kw in
                        ["cookie", "informa", "privacy", "copyright", "newsletter",
                         "register", "account", "verified"])):
                desc = txt[:1000]
                break
    data["Company Description"] = desc

    # Sales Markets, Primary Business Activity, Categories
    # These are stored in HTML table rows as <th>Label</th><td>Value</td>
    for th in soup.find_all("th"):
        label = th.get_text(strip=True).rstrip(":")
        td = th.find_next_sibling("td")
        if not td:
            continue

        label_lower = label.lower()

        if "sales market" in label_lower:
            data["Sales Markets"] = td.get_text(strip=True)

        elif "primary business" in label_lower:
            data["Primary Business Activity"] = td.get_text(strip=True)

        elif "affiliated categor" in label_lower or label_lower == "categories":
            # Categories are listed as clickable links, extract each one
            # Filter out "More" and "Less" toggle buttons
            cat_links = td.find_all("a")
            cats = [a.get_text(strip=True) for a in cat_links
                    if a.get_text(strip=True) not in ("More", "Less", "")]
            if cats:
                data["Categories"] = ", ".join(cats)
            else:
                # Fallback: get raw text and clean up separators
                raw = td.get_text(strip=True)
                raw = re.sub(r"(More|Less)$", "", raw).strip().rstrip("|").strip()
                data["Categories"] = raw.replace("|", ", ")

    # Contact info: Address, Email, Telephone, Website
    # These are inside <div class="company-information"> with <h3> labels
    info_div = soup.find("div", class_="company-information")
    if info_div:
        for h3 in info_div.find_all("h3"):
            label = h3.get_text(strip=True)
            next_el = h3.find_next_sibling()
            if not next_el:
                continue

            if label == "Address":
                data["Address"] = next_el.get_text(strip=True)

            elif label == "Email":
                # CloudFlare protects emails with encoded spans
                cf_span = next_el.find("span", class_="__cf_email__")
                if cf_span:
                    encoded = cf_span.get("data-cfemail", "")
                    data["Email"] = decode_cf_email(encoded)
                else:
                    href = next_el.get("href", "")
                    if href.startswith("mailto:"):
                        data["Email"] = href.replace("mailto:", "").strip()
                    else:
                        data["Email"] = next_el.get_text(strip=True)

            elif label == "Telephone":
                href = next_el.get("href", "")
                if href.startswith("tel:"):
                    data["Telephone"] = href.replace("tel:", "").strip()
                else:
                    data["Telephone"] = next_el.get_text(strip=True)

            elif label == "Website":
                href = next_el.get("href", "")
                if href.startswith("http"):
                    data["Website"] = href
                else:
                    data["Website"] = next_el.get_text(strip=True)

    # Events: listed in <div class="company-event"> sections
    events = []
    event_divs = soup.find_all("div", class_="company-event")
    for ev in event_divs:
        event_link = ev.find("a")
        if event_link:
            # Use space separator to avoid date text running together
            txt = event_link.get_text(" ", strip=True)
            txt = re.sub(r"\s+", " ", txt).strip()
            if txt:
                events.append(txt)

    # Fallback: look for events under "Upcoming events" heading
    if not events:
        upcoming = soup.find(string=re.compile(r"upcoming events", re.I))
        if upcoming:
            parent = upcoming.parent
            if parent:
                container = parent.parent
                if container:
                    for a in container.find_all("a"):
                        href = a.get("href", "")
                        if "exhibitor" in href or "event" in href.lower():
                            txt = a.get_text(strip=True)
                            if txt:
                                events.append(txt)
    data["Events"] = "; ".join(events) if events else ""

    # Fallback: try to find email/tel/website from full page if not in company-information
    if not data.get("Email"):
        cf_emails = soup.find_all("span", class_="__cf_email__")
        for cf in cf_emails:
            decoded = decode_cf_email(cf.get("data-cfemail", ""))
            if decoded and "@" in decoded:
                data["Email"] = decoded
                break

    if not data.get("Telephone"):
        tel_link = soup.find("a", href=re.compile(r"^tel:"))
        if tel_link:
            data["Telephone"] = tel_link["href"].replace("tel:", "").strip()

    if not data.get("Website"):
        for a in soup.find_all("a", class_="webLink"):
            href = a.get("href", "")
            # Only use external website links, not ingredientsnetwork.com links
            if href.startswith("http") and "ingredientsnetwork" not in href:
                data["Website"] = href
                break

    # Make sure all 10 required fields exist in the dict
    for field in ["Company Name", "Company Description", "Sales Markets",
                  "Primary Business Activity", "Categories", "Events",
                  "Address", "Email", "Telephone", "Website"]:
        if field not in data:
            data[field] = ""

    return data


def scrape_all_companies(company_urls):
    """Step 3: Visit each company page and extract all required fields."""
    print(f"\nStep 3: Extracting data from {len(company_urls)} company pages...")
    print("  (This may take a while - ~0.5s per page)")

    session = requests.Session()
    session.headers.update(HEADERS)

    all_data = []
    errors = 0

    for i, url in enumerate(company_urls):
        data = extract_company_data(url, session)

        if data is None:
            errors += 1
        else:
            all_data.append(data)

        # Print progress every 100 companies
        if (i + 1) % 100 == 0:
            print(f"  [{i+1}/{len(company_urls)}] Extracted {len(all_data)} companies "
                  f"({errors} errors)")

        # Polite delay to avoid overwhelming the server
        time.sleep(0.3)

    print(f"\nExtraction complete: {len(all_data)} companies, {errors} errors")
    return all_data


def save_raw_data(all_data):
    """Save raw extracted data to JSON before cleaning."""
    print(f"\nSaving raw data to {TEMP_JSON}...")
    with open(TEMP_JSON, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(all_data)} raw records")


def clean_and_save(all_data):
    """Step 4-6: Clean data, remove duplicates, and save to CSV."""
    print("\nStep 4: Applying data cleaning rules...")

    cleaned = []
    seen_names = set()

    for row in all_data:
        name = row.get("Company Name", "").strip()

        # Skip entries with no company name
        if not name:
            continue

        # Remove duplicate companies (case-insensitive comparison)
        name_key = name.lower()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)

        # Clean all string fields: normalize whitespace, replace pipe separators, fill empty with N/A
        for key in row:
            if isinstance(row[key], str):
                row[key] = " ".join(row[key].split())
                row[key] = row[key].replace("|", ", ")
                if not row[key].strip():
                    row[key] = "N/A"

        cleaned.append(row)

    print(f"  After cleaning: {len(cleaned)} unique companies "
          f"(removed {len(all_data) - len(cleaned)} duplicates/empty)")

    # Save temporary raw CSV (before final cleaning, as required by Step 4)
    fieldnames = [
        "Company Name", "Company Description", "Sales Markets",
        "Primary Business Activity", "Categories", "Events",
        "Address", "Email", "Telephone", "Website", "URL"
    ]

    print(f"\n  Saving temporary data to {TEMP_CSV}...")
    with open(TEMP_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(cleaned)

    # Step 6: Save final clean CSV (without the URL column)
    print(f"\nStep 6: Saving final data to {OUTPUT_CSV}...")
    final_fieldnames = [
        "Company Name", "Company Description", "Sales Markets",
        "Primary Business Activity", "Categories", "Events",
        "Address", "Email", "Telephone", "Website"
    ]
    # utf-8-sig adds BOM for proper Excel compatibility
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=final_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(cleaned)

    print(f"  Saved {len(cleaned)} rows to {OUTPUT_CSV}")
    return cleaned


def answer_questions(rows):
    """Answer the 5 submission questions from the extracted data."""
    print("\n" + "=" * 60)
    print("ANSWERS TO SUBMISSION QUESTIONS")
    print("=" * 60)

    # (i) Total unique ingredients/categories across all companies
    all_ingredients = set()
    for r in rows:
        cats = r.get("Categories", "")
        for c in cats.split(","):
            c = c.strip()
            if c and c != "N/A":
                all_ingredients.add(c.lower())
    print(f"\n(i) Total unique ingredients/categories: {len(all_ingredients)}")

    # (ii) Companies that deal in finished products
    finished_count = 0
    for r in rows:
        activity = r.get("Primary Business Activity", "").lower()
        cats = r.get("Categories", "").lower()
        if "finished" in activity or "finished" in cats:
            finished_count += 1
    print(f"(ii) Companies with finished products: {finished_count}")

    # (iii) Companies with herbs and spices in their categories
    herbs_spices = [
        r for r in rows
        if "herb" in r.get("Categories", "").lower()
        or "spice" in r.get("Categories", "").lower()
    ]
    print(f"(iii) Companies with herbs and spices: {len(herbs_spices)}")

    # (iv) Companies with physical delivery formats
    physical = [
        r for r in rows
        if "physical" in r.get("Categories", "").lower()
    ]
    print(f"(iv) Companies with physical delivery formats: {len(physical)}")

    # (v) Companies in Cognitive & Mental Health category
    cognitive = [
        r for r in rows
        if "cognitive" in r.get("Categories", "").lower()
        or "mental health" in r.get("Categories", "").lower()
    ]
    print(f"(v) Companies in Cognitive & Mental Health: {len(cognitive)}")

    print("=" * 60)


if __name__ == "__main__":
    print("Ingredients Network Data Scraper")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 60)

    # Use --reprocess flag to regenerate CSV from saved JSON without re-scraping
    if "--reprocess" in sys.argv:
        print("Reprocessing from saved raw JSON...")
        with open(TEMP_JSON, "r", encoding="utf-8") as f:
            all_data = json.load(f)
        print(f"  Loaded {len(all_data)} records from {TEMP_JSON}")
    else:
        company_urls = get_all_company_urls()
        all_data = scrape_all_companies(company_urls)
        save_raw_data(all_data)

    rows = clean_and_save(all_data)
    answer_questions(rows)

    print(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
