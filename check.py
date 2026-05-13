#!/usr/bin/env python3
"""
Mac mini refurb watcher.

What this script does:
- Fetches Apple's refurbished Mac mini page.
- Looks for product cards that mention Mac mini and M4.
- Alerts only when price <= PRICE_CAP.
- Sends notifications to Slack and/or ntfy.sh.
- Supports a forced test ping so you can prove notifications work.
- Saves state.json so you do not get repeated alerts for the same hit.

No third-party Python dependencies.
"""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

APPLE_URL = os.environ.get(
    "APPLE_URL",
    "https://www.apple.com/shop/refurbished/mac/mac-mini",
).strip()
PRICE_CAP = int(os.environ.get("PRICE_CAP", "700"))
STATE_PATH = Path(os.environ.get("STATE_PATH", "state.json"))

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
SLACK_MENTION_USER_IDS = os.environ.get("SLACK_MENTION_USER_IDS", "").strip()

# Optional fallback/easier notification path.
# Create a random topic like: andrew-macmini-watch-837462
# Then subscribe in the ntfy app or browser at https://ntfy.sh/<topic>
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

TEST_PING = os.environ.get("TEST_PING", "0").strip() == "1"
FORCE_NOTIFY = os.environ.get("FORCE_NOTIFY", "0").strip() == "1"
DEBUG_DUMP = os.environ.get("DEBUG_DUMP", "0").strip() == "1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            log(f"[fetch] {url} -> {resp.status} ({len(body):,} bytes)")
            return body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:500]
        log(f"[fetch] HTTP error {e.code}: {body}")
        return ""
    except (urllib.error.URLError, TimeoutError) as e:
        log(f"[fetch] network error: {e}")
        return ""


def strip_tags(value: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html_lib.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def parse_price(text: str) -> int | None:
    # Examples: $599.00, $1,099.00
    match = re.search(r"\$\s*([0-9][0-9,]{1,5})(?:\.\d{2})?", text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def find_product_chunks(raw_html: str) -> list[str]:
    """Try to split the Apple page into product-ish chunks.

    Apple's markup changes, so this intentionally uses a few loose strategies.
    If none work, we fall back to windows around every 'Refurbished ... Mac mini'.
    """
    chunks: list[str] = []

    # Common Apple retail pages contain product tiles/cards with useful local text.
    for pattern in [
        r"<li[^>]+(?:rf-serp-product|rf-searchresult|as-producttile|product)[^>]*>[\s\S]*?</li>",
        r"<div[^>]+(?:rf-serp-product|rf-searchresult|as-producttile|product)[^>]*>[\s\S]*?</div>\s*</div>",
    ]:
        chunks.extend(re.findall(pattern, raw_html, flags=re.I))

    # Fallback: capture a neighborhood around each likely title.
    if not chunks:
        title_re = re.compile(r"Refurbished[\s\S]{0,300}?Mac mini[\s\S]{0,300}?M4", re.I)
        for match in title_re.finditer(raw_html):
            start = max(0, match.start() - 1200)
            end = min(len(raw_html), match.end() + 2200)
            chunks.append(raw_html[start:end])

    # Deduplicate exact chunks while keeping order.
    seen = set()
    unique = []
    for chunk in chunks:
        key = chunk[:500]
        if key not in seen:
            seen.add(key)
            unique.append(chunk)
    return unique


def extract_hits(raw_html: str) -> list[dict[str, Any]]:
    mac_mini_count = len(re.findall(r"Mac mini", raw_html, re.I))
    m4_count = len(re.findall(r"\bM4\b", raw_html))
    prices = sorted({p for p in re.findall(r"\$\s*([0-9][0-9,]{1,5}(?:\.\d{2})?)", raw_html)})
    log(
        "[apple] "
        f"'Mac mini' x{mac_mini_count}, 'M4' x{m4_count}, "
        f"distinct prices: {prices[:20]}{'...' if len(prices) > 20 else ''}"
    )

    chunks = find_product_chunks(raw_html)
    log(f"[parse] product-ish chunks found: {len(chunks)}")

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()

    for chunk in chunks:
        text = strip_tags(chunk)
        if not re.search(r"Mac mini", text, re.I):
            continue
        if not re.search(r"\bM4\b", text):
            continue
        price = parse_price(text)
        if price is None:
            continue

        # Keep the title readable and stable.
        title_match = re.search(r"(Refurbished\s+.*?Mac mini.*?M4.*?)(?=\s+\$|\s+From\s+\$|$)", text, re.I)
        if title_match:
            title = title_match.group(1)
        else:
            title = text[:180]
        title = re.sub(r"\s+", " ", title).strip()[:180]

        # Try to find a product URL nearby. Fallback to page URL.
        href_match = re.search(r'href="([^"]+)"', chunk)
        url = APPLE_URL
        if href_match:
            href = html_lib.unescape(href_match.group(1))
            if href.startswith("/"):
                url = "https://www.apple.com" + href
            elif href.startswith("http"):
                url = href

        key = f"{title}|{price}"
        if key in seen:
            continue
        seen.add(key)

        if price <= PRICE_CAP or FORCE_NOTIFY:
            hits.append(
                {
                    "retailer": "Apple Refurb",
                    "variant": title,
                    "price": price,
                    "url": url,
                }
            )

    if DEBUG_DUMP:
        Path("debug_apple_refurb.html").write_text(raw_html, encoding="utf-8")
        log("[debug] wrote debug_apple_refurb.html")

    return hits


def check_apple_refurb() -> list[dict[str, Any]]:
    raw_html = fetch(APPLE_URL)
    if not raw_html:
        return []
    return extract_hits(raw_html)


def slack_mentions() -> str:
    if not SLACK_MENTION_USER_IDS:
        return ""
    ids = [u.strip() for u in SLACK_MENTION_USER_IDS.split(",") if u.strip()]
    if not ids:
        return ""
    return " ".join(f"<@{user_id}>" for user_id in ids) + " "


def format_message(hit: dict[str, Any], *, test: bool = False) -> str:
    prefix = "✅ TEST: " if test else ":rotating_light: "
    cap_text = f"target <= ${PRICE_CAP}"
    return (
        f"{slack_mentions()}{prefix}Mac mini watch alert ({cap_text})\n"
        f"{hit['retailer']} — {hit['variant']}\n"
        f"Price: ${hit['price']}\n"
        f"{hit['url']}"
    )


def post_slack(text: str) -> bool:
    if not SLACK_WEBHOOK_URL:
        log("[slack] skipped: SLACK_WEBHOOK_URL is empty/missing")
        return False

    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            response_body = resp.read().decode("utf-8", errors="ignore")
            log(f"[slack] status={resp.status}, body={response_body!r}")
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        response_body = e.read().decode("utf-8", errors="ignore")
        log(f"[slack] HTTP error status={e.code}, body={response_body!r}")
        return False
    except (urllib.error.URLError, TimeoutError) as e:
        log(f"[slack] network error: {e}")
        return False


def post_ntfy(text: str) -> bool:
    if not NTFY_TOPIC:
        log("[ntfy] skipped: NTFY_TOPIC is empty/missing")
        return False

    topic = NTFY_TOPIC.strip().strip("/")
    url = f"https://ntfy.sh/{topic}"
    req = urllib.request.Request(
        url,
        data=text.encode("utf-8"),
        headers={
            "Title": "Mac mini watch",
            "Priority": "4",
            "Tags": "computer,apple",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            response_body = resp.read().decode("utf-8", errors="ignore")[:200]
            log(f"[ntfy] status={resp.status}, body={response_body!r}")
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        response_body = e.read().decode("utf-8", errors="ignore")
        log(f"[ntfy] HTTP error status={e.code}, body={response_body!r}")
        return False
    except (urllib.error.URLError, TimeoutError) as e:
        log(f"[ntfy] network error: {e}")
        return False


def notify(hit: dict[str, Any], *, test: bool = False) -> bool:
    text = format_message(hit, test=test)
    ok_slack = post_slack(text)
    ok_ntfy = post_ntfy(text)

    if not ok_slack and not ok_ntfy:
        log("[notify] no notification destination succeeded")
        log(f"[notify] message was:\n{text}")
        return False
    return True


def signature(hit: dict[str, Any]) -> str:
    return f"{hit['retailer']}|{hit['variant']}|{hit['price']}"


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log("[state] state.json was invalid JSON; ignoring it")
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_ping() -> int:
    log("[test] sending notification test ping")
    test_hit = {
        "retailer": "TEST",
        "variant": "end-to-end GitHub Actions notification test",
        "price": PRICE_CAP,
        "url": APPLE_URL,
    }
    ok = notify(test_hit, test=True)
    return 0 if ok else 2


def main() -> int:
    log(f"[config] PRICE_CAP=${PRICE_CAP}, TEST_PING={TEST_PING}, FORCE_NOTIFY={FORCE_NOTIFY}")
    log(f"[config] Slack configured: {bool(SLACK_WEBHOOK_URL)}; ntfy configured: {bool(NTFY_TOPIC)}")

    if TEST_PING:
        return test_ping()

    try:
        hits = check_apple_refurb()
    except Exception as e:  # Keep scheduled jobs from crashing silently.
        log(f"[error] check failed: {e!r}")
        return 1

    print(f"hits this run: {len(hits)}")
    for hit in hits:
        print(f"  - {signature(hit)} -> {hit['url']}")

    previous = load_state()
    current = {signature(hit): hit for hit in hits}
    new_keys = sorted(set(current) - set(previous))

    for key in new_keys:
        log(f"[alert] new hit: {key}")
        notify(current[key])
        time.sleep(1)

    save_state(current)
    return 0


if __name__ == "__main__":
    sys.exit(main())