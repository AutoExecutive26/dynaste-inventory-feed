"""
Dynaste Acquisitions - Sunshine Auto inventory feed.

Completely separate from the Auto Executive script (update_inventory.py):
it shares no code and writes only its own file, dynaste_sunshine_inventory.json.
It keeps Sunshine Auto's ORIGINAL photos (no background removal) and a link
to each car's listing, for the Sunshine Auto tab on dynasteacquisitions.com.
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ---- Settings ----------------------------------------------------------------
BASE = "https://sunshineautobrokers.com"
LIST_URL = BASE + "/legacy"
OUTPUT = "dynaste_sunshine_inventory.json"
MIN_EXPECTED = 30          # refuse to overwrite the feed with fewer cars than this
MAX_PHOTOS = 20            # photos listed per car
DELAY_SECONDS = 0.6        # pause between requests, to be polite to the source
# -------------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TWO_WORD_MAKES = ["Land Rover", "Alfa Romeo", "Aston Martin", "Rolls Royce"]
SKIP_IMG = re.compile(
    r"logo|icon|sprite|favicon|placeholder|no-?image|spacer|pixel|"
    r"facebook|twitter|instagram|badge|banner|carfax|autocheck",
    re.I,
)

CATEGORY_RULES = [
    ("RVs", ["forest river", "winnebago", "motorhome", "camper"]),
    ("Vans", ["pacifica", "town & country", "caravan", "odyssey", "sienna",
              "transit", "promaster", "ram c/v", "vanagon", "sprinter",
              "econoline", "express van"]),
    ("Trucks", ["silverado", "sierra", "ram 1500", "ram 2500", "ram 3500",
                "dodge ram", "f150", "f-150", "f250", "f350", "tacoma",
                "tundra", "chevrolet s10", "ranger", "colorado", "frontier",
                "titan", "ridgeline"]),
    ("Sports & Classics", ["corvette", "f-type", "124 spider", "450 sl",
                           "nova", "eldorado", "bentley", "bently", "scion tc",
                           "mustang", "camaro", "challenger", "convertible"]),
    ("SUVs", ["rdx", "q7", "sq5", "q5", "bmw x", "enclave", "escalade", "srx",
              "equinox", "santa fe", "qx80", "qx60", "cherokee", "wrangler",
              "sportage", "gx 460", "rx 350", "gla ", "murano", "rogue",
              "fj cruiser", "4runner", "rav4", "ford flex", "cr-v", "bronco",
              "explorer", "tahoe", "suburban", "yukon", "kia soul",
              "highlander", "pathfinder", "compass", "patriot", "pilot"]),
]
CATEGORY_OVERRIDES = {
    # "2010 Chevrolet HHR": "SUVs",
}


def fetch(session, url, tries=3):
    for attempt in range(1, tries + 1):
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.text
            print(f"  HTTP {r.status_code} for {url} (try {attempt})")
        except requests.RequestException as exc:
            print(f"  Error for {url}: {exc} (try {attempt})")
        time.sleep(2 * attempt)
    return None


def list_cars(list_html):
    soup = BeautifulSoup(list_html, "html.parser")
    cars = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"/legacy/view/(\d+)", a["href"])
        if m and m.group(1) not in cars:
            cars[m.group(1)] = urljoin(LIST_URL, a["href"])
    return cars


def find_title(soup):
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = " ".join(tag.get_text(" ", strip=True).split())
        if re.match(r"^(19|20)\d{2}\s+\S+", text):
            words = []
            for w in text.split():
                if w.isalpha() and w.isupper() and len(w) > 3:
                    w = w.capitalize()
                words.append(w)
            return " ".join(words)
    return None


def split_title(title):
    m = re.match(r"^((?:19|20)\d{2})\s+(.*)$", title)
    if not m:
        return None, "", title
    year, rest = int(m.group(1)), m.group(2)
    make = None
    for two in TWO_WORD_MAKES:
        if rest.lower().startswith(two.lower() + " ") or rest.lower() == two.lower():
            make = rest[: len(two)]
            break
    if make is None:
        make = rest.split(" ", 1)[0]
    return year, make, rest[len(make):].strip()


def find_mileage(page_text):
    for pat in (r"(?:mileage|odometer|miles)\s*[:\-]?\s*([\d,]{3,9})\b",
                r"\b([\d,]{4,9})\s*(?:miles|mi\b)"):
        m = re.search(pat, page_text, re.I)
        if m:
            try:
                value = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if 0 < value < 999999:
                return value
    return None


def find_photos(soup, raw_html, car_key):
    """Every photo of this car, main photo first, as the dealer's own links."""
    pattern = (r"https?://[^\s\"'<>\\]*/service/picture/\d+/"
               + re.escape(str(car_key)) + r"/[0-9a-f]{20,}")
    hits = []
    for m in re.finditer(pattern, raw_html):
        url = m.group(0).replace("http://", "https://", 1)
        if url not in hits:
            hits.append(url)
    main = None
    for meta in soup.find_all("meta", attrs={"property": "og:image"}):
        main = (meta.get("content") or "").replace("http://", "https://", 1)
        break
    if main in hits:
        hits.remove(main)
        hits.insert(0, main)
    elif not hits and main and not SKIP_IMG.search(main):
        hits = [main]
    return hits


def categorize(year, make, model, title):
    if title in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[title]
    text = f"{make} {model} ".lower()
    for category, words in CATEGORY_RULES:
        if any(w in text for w in words):
            return category
    if year and year <= 1975:
        return "Sports & Classics"
    return "Cars"


def main():
    session = requests.Session()
    print(f"Fetching list: {LIST_URL}")
    list_html = fetch(session, LIST_URL)
    if not list_html:
        print("ERROR: could not load the list page. Feed left unchanged.")
        sys.exit(1)

    cars = list_cars(list_html)
    print(f"Found {len(cars)} car links on the list page.")

    vehicles = []
    for index, (source_id, url) in enumerate(cars.items(), start=1):
        time.sleep(DELAY_SECONDS)
        html = fetch(session, url)
        if not html:
            print(f"[{index}/{len(cars)}] SKIPPED {url} (could not load)")
            continue
        soup = BeautifulSoup(html, "html.parser")
        title = find_title(soup)
        if not title:
            print(f"[{index}/{len(cars)}] SKIPPED {url} (no title found)")
            continue
        year, make, model = split_title(title)
        photos = find_photos(soup, html, source_id)[:MAX_PHOTOS]
        mileage = find_mileage(soup.get_text(" ", strip=True))
        vehicles.append({
            "id": f"ss-{source_id}",
            "title": title,
            "year": year,
            "make": make,
            "model": model,
            "mileage": mileage,
            "category": categorize(year, make, model, title),
            "photos": photos,
            "url": url,
        })
        print(f"[{index}/{len(cars)}] {title} | {len(photos)} photos | miles: {mileage}")

    print(f"\nBuilt {len(vehicles)} vehicles.")
    if len(vehicles) < MIN_EXPECTED:
        print(f"ERROR: only {len(vehicles)} cars (expected at least {MIN_EXPECTED}). "
              "Feed left unchanged.")
        sys.exit(1)

    vehicles.sort(key=lambda v: (v["make"].lower(), v["model"].lower(), v["year"] or 0))
    try:
        with open(OUTPUT, encoding="utf-8") as f:
            if json.load(f).get("vehicles") == vehicles:
                print("No changes since last run.")
                return
    except (OSError, ValueError):
        pass
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump({"updated_at": datetime.now(timezone.utc).isoformat(),
                   "count": len(vehicles), "vehicles": vehicles},
                  f, indent=2, ensure_ascii=False)
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
