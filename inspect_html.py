"""
Diagnostic script to inspect the HKJC race card HTML and find correct CSS selectors.
"""
import json
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from config import URLS, HEADERS

# Fetch the page
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(extra_http_headers=HEADERS)
    url = "https://racing.hkjc.com/en-us/local/information/racecard?racedate=2026%2F03%2F25&Racecourse=HV"
    page.goto(url, wait_until="load", timeout=30000)
    page.wait_for_timeout(5000)
    html = page.content()
    browser.close()

# Save HTML for inspection
html_file = Path("data/raw/2026-03-25_HV/inspect.html")
html_file.write_text(html)
print(f"✓ Saved HTML to {html_file}")

# Parse and look for potential selectors
soup = BeautifulSoup(html, "html.parser")

# Find common container patterns
print("\n" + "="*70)
print("POTENTIAL RACE CONTAINERS")
print("="*70)

patterns = {
    "divs with 'race' in class": "div[class*='race' i]",
    "divs with 'card' in class": "div[class*='card' i]",
    "divs with 'race-detail'": "div[class*='detail' i]",
    "tables with 'race'": "table[class*='race' i]",
    "all classes containing patterns": None,
}

for desc, selector in patterns.items():
    if selector:
        elements = soup.select(selector)
        if elements:
            print(f"\n{desc} ({selector}):")
            print(f"  Found {len(elements)} element(s)")
            for i, el in enumerate(elements[:2]):  # Show first 2
                classes = el.get('class', [])
                print(f"    [{i}] {el.name} class={classes}")

# Show top-level structure
print("\n" + "="*70)
print("TOP-LEVEL STRUCTURE")
print("="*70)
main = soup.find('main') or soup.find('div', class_=lambda x: x and 'main' in x.lower())
if main:
    children = list(main.children)[:5]
    print(f"Main content children: {len(children)} items")
    for child in children:
        if hasattr(child, 'name'):
            classes = child.get('class', []) if hasattr(child, 'get') else []
            print(f"  {child.name} class={classes}")

# Try to find race numbers or dates
print("\n" + "="*70)
print("TEXT CONTENT SEARCH")
print("="*70)
text = soup.get_text()
if "Race" in text:
    print("✓ Found 'Race' in page text")
if "2026-03-25" in text or "25/03/2026" in text:
    print("✓ Found date in page text")

# Count various tag types
tag_counts = {}
for tag in soup.find_all(True):
    tag_name = tag.name
    tag_counts[tag_name] = tag_counts.get(tag_name, 0) + 1

print(f"\nTag distribution: {dict(sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10])}")
