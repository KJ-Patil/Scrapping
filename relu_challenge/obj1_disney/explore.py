"""Explore the Disney Cruise site: list JSON responses and save the page HTML."""
import time
from playwright.sync_api import sync_playwright

URL = "https://disneycruise.disney.go.com/en-in/"

def on_response(resp):
    ctype = resp.headers.get("content-type", "")
    if "json" in ctype:
        print(resp.status, resp.url[:150])

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.on("response", on_response)
    page.goto(URL)
    page.wait_for_timeout(8000)

    # Try to dismiss a consent/agree popup if one exists
    for label in ["Agree", "Accept", "I Agree", "Accept All"]:
        try:
            page.get_by_role("button", name=label).click(timeout=2000)
            break
        except Exception:
            pass

    print("---- clicking View Dates ----")
    page.get_by_text("View Dates").first.click()
    page.wait_for_timeout(10000)

    page.screenshot(path="after_click.png", full_page=False)
    with open("page.html", "w", encoding="utf-8") as f:
        f.write(page.content())
    print("Saved page.html and after_click.png")
    time.sleep(5)
    browser.close()