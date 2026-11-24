"""plugins/web_scraper.py — Structured Playwright + BS4 + lxml scraper & shopping price comparator."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

PLUGIN = {
    "name": "web_scraper",
    "brief": "Structured CSS/XPath web scraper and multi-store shopping price comparison tool",
    "description": (
        "Scrapes webpages via HTTPX or headless Playwright into structured CSS/XPath fields, "
        "HTML tables, and links wrapped in <<<UNTRUSTED>>> delimiters, and runs multi-store "
        "shopping price comparisons ranked by best price. Actions: status, scrape, shopping_compare."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | scrape | shopping_compare (default: status)",
            },
            "url": {
                "type": "STRING",
                "description": "Target webpage URL for scrape.",
            },
            "html": {
                "type": "STRING",
                "description": "Optional raw HTML payload to parse without fetching.",
            },
            "css": {
                "type": "STRING",
                "description": "CSS selector string for scrape.",
            },
            "xpath": {
                "type": "STRING",
                "description": "XPath selector string for scrape.",
            },
            "fields": {
                "type": "OBJECT",
                "description": "Mapping of output field name -> CSS selector or {'xpath': ...}.",
            },
            "extract_tables": {
                "type": "BOOLEAN",
                "description": "Extract HTML <table> elements into row lists.",
            },
            "use_browser": {
                "type": "BOOLEAN",
                "description": "Render dynamic JavaScript via headless Playwright Chromium.",
            },
            "query": {
                "type": "STRING",
                "description": "Product name for action='shopping_compare'.",
            },
            "store_snapshots": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Optional offline list of {'store': str, 'url': str, 'html': str, 'price_css': str, 'title_css': str}.",
            },
        },
        "required": [],
    },
}


def _fetch_html(url: str, *, use_browser: bool = False, timeout_s: float = 12.0) -> str:
    if use_browser:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_s * 1000))
                return page.content()
            finally:
                browser.close()
    import httpx
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Mark-LVII/1.0"}
    with httpx.Client(timeout=timeout_s, follow_redirects=True, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def extract_structured(
    html_text: str,
    *,
    css: str = "",
    xpath: str = "",
    fields: dict | None = None,
    extract_tables: bool = False,
    limit: int = 25,
) -> dict:
    """Parse HTML deterministically via BeautifulSoup4 + lxml CSS/XPath."""
    from bs4 import BeautifulSoup
    from lxml import html as lxml_html

    soup = BeautifulSoup(html_text or "", "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    out: dict = {"title": title}

    if css:
        matches = [el.get_text(" ", strip=True) for el in soup.select(css)[:limit]]
        out["css_matches"] = [m for m in matches if m]

    if xpath:
        tree = lxml_html.fromstring(html_text or "<html></html>")
        nodes = tree.xpath(xpath)[:limit]
        x_vals = []
        for n in nodes:
            if isinstance(n, str):
                s = n.strip()
            elif hasattr(n, "text_content"):
                s = n.text_content().strip()
            else:
                s = str(n).strip()
            if s:
                x_vals.append(s)
        out["xpath_matches"] = x_vals

    if isinstance(fields, dict) and fields:
        tree = lxml_html.fromstring(html_text or "<html></html>") if not xpath else lxml_html.fromstring(html_text or "<html></html>")
        extracted: dict[str, list[str]] = {}
        for name, spec in fields.items():
            if isinstance(spec, dict) and spec.get("xpath"):
                nodes = tree.xpath(str(spec["xpath"]))[:limit]
                extracted[name] = [
                    (n.strip() if isinstance(n, str) else n.text_content().strip())
                    for n in nodes
                    if (n.strip() if isinstance(n, str) else n.text_content().strip())
                ]
            else:
                sel = str(spec.get("css") if isinstance(spec, dict) else spec)
                extracted[name] = [el.get_text(" ", strip=True) for el in soup.select(sel)[:limit] if el.get_text(strip=True)]
        out["fields"] = extracted

    if extract_tables:
        tables = []
        for tbl in soup.find_all("table")[:5]:
            rows = []
            for tr in tbl.find_all("tr")[:40]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
                if any(cells):
                    rows.append(cells)
            if rows:
                tables.append(rows)
        out["tables"] = tables

    if not css and not xpath and not fields and not extract_tables:
        paras = [p.get_text(" ", strip=True) for p in soup.find_all(["h1", "h2", "p"])[:limit]]
        out["summary_blocks"] = [x for x in paras if x]

    return out


def _parse_price(raw_text: str) -> tuple[float | None, str]:
    text = str(raw_text or "")
    m = re.search(r"(₹|INR|Rs\.?|\$|USD|€|EUR|£)\s*([\d,]+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if not m:
        m2 = re.search(r"([\d,]+(?:\.\d{1,2})?)", text)
        if not m2:
            return None, ""
        val_s = m2.group(1).replace(",", "")
        cur = "INR" if ("₹" in text or "rs" in text.lower()) else "USD"
        try:
            return float(val_s), cur
        except ValueError:
            return None, ""
    sym = m.group(1).upper()
    cur = "INR" if sym in ("₹", "INR", "RS", "RS.") else ("USD" if sym in ("$", "USD") else sym)
    try:
        return float(m.group(2).replace(",", "")), cur
    except ValueError:
        return None, ""


def shopping_compare(query: str, *, store_snapshots: list[dict] | None = None, use_browser: bool = True) -> str:
    """Compare product prices across stores and rank cheapest first."""
    q = str(query or "").strip()
    if not q and not store_snapshots:
        return "Provide query='...' to compare product prices across stores."

    targets = store_snapshots if isinstance(store_snapshots, list) and store_snapshots else [
        {
            "store": "Amazon.in",
            "url": f"https://www.amazon.in/s?k={quote_plus(q)}",
            "title_css": "h2 span",
            "price_css": ".a-price .a-offscreen",
        },
        {
            "store": "Flipkart",
            "url": f"https://www.flipkart.com/search?q={quote_plus(q)}",
            "title_css": "div.KzDlHZ, a.wjcEIp",
            "price_css": "div.Nx9bqj",
        },
        {
            "store": "Croma",
            "url": f"https://www.croma.com/searchB?q={quote_plus(q)}%3Arelevance",
            "title_css": "h3.product-title",
            "price_css": "span.amount",
        },
    ]

    offers: list[dict] = []
    for st in targets:
        store_name = str(st.get("store") or "Store")
        html_text = st.get("html")
        url = str(st.get("url") or "")
        err = ""
        if not html_text and url:
            try:
                html_text = _fetch_html(url, use_browser=use_browser, timeout_s=10.0)
            except Exception as exc:
                err = str(exc)
                html_text = ""

        if not html_text:
            offers.append({
                "store": store_name, "title": q or "unavailable", "price": None,
                "currency": "", "raw_price": f"fetch failed ({err[:60]})" if err else "unavailable",
            })
            continue

        ext = extract_structured(
            html_text,
            fields={
                "titles": st.get("title_css") or "h2, h3, .title",
                "prices": st.get("price_css") or ".price, .amount",
            },
            limit=3,
        )
        flds = ext.get("fields") or {}
        title_hit = (flds.get("titles") or [q or "Item"])[0]
        price_hit = (flds.get("prices") or [""])[0]
        val, cur = _parse_price(price_hit)
        offers.append({
            "store": store_name,
            "title": title_hit[:70],
            "price": val,
            "currency": cur,
            "raw_price": price_hit or "no price found",
        })

    ranked = sorted(offers, key=lambda o: (o["price"] is None, o["price"] if o["price"] is not None else 1e18))
    lines = [
        f"<<<UNTRUSTED>>>",
        f"Shopping Price Comparison for: {q or 'Snapshot Query'}",
        "| Rank | Store | Price | Listing |",
        "| --- | --- | --- | --- |",
    ]
    for i, o in enumerate(ranked, 1):
        pstr = f"{o['currency']} {o['price']:,.2f}" if o["price"] is not None else o["raw_price"]
        lines.append(f"| {i} | {o['store']} | {pstr} | {o['title']} |")
    best = next((o for o in ranked if o["price"] is not None), None)
    if best:
        lines.append(f"Best Price: {best['store']} at {best['currency']} {best['price']:,.2f}.")
    lines.append("<<<END_UNTRUSTED>>>")
    return "\n".join(lines)


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for structured web scraping & price comparison."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    if action == "status":
        pw_ok = False
        try:
            import playwright  # noqa: F401
            pw_ok = True
        except ImportError:
            pass
        return f"Web Scraper ready (bs4+lxml=ok, playwright={'ok' if pw_ok else 'missing'})."

    if action in ("shopping_compare", "compare_prices"):
        return shopping_compare(
            str(params.get("query") or ""),
            store_snapshots=params.get("store_snapshots"),
            use_browser=bool(params.get("use_browser", False)),
        )

    if action == "scrape":
        raw_html = str(params.get("html") or "")
        url = str(params.get("url") or "").strip()
        if not raw_html:
            if not url:
                return "Provide url='...' or html='...' to scrape."
            raw_html = _fetch_html(url, use_browser=bool(params.get("use_browser", False)))
        data = extract_structured(
            raw_html,
            css=str(params.get("css") or ""),
            xpath=str(params.get("xpath") or ""),
            fields=params.get("fields"),
            extract_tables=bool(params.get("extract_tables", False)),
        )
        import json
        return "<<<UNTRUSTED>>>\n" + json.dumps(data, indent=2, ensure_ascii=False) + "\n<<<END_UNTRUSTED>>>"

    return f"Unknown web_scraper action '{action}'. Use: status, scrape, shopping_compare."
