"""plugins/market_finance.py — Live Stocks, Indices, Crypto & Forex market quotes."""

from __future__ import annotations

PLUGIN = {
    "name": "market_finance",
    "brief": "Live market prices for stocks, indices, crypto, and forex",
    "description": (
        "Fetches real-time quotes and daily percentage changes for stocks, global indices (^GSPC, ^NSEI), "
        "cryptocurrencies (BTC-USD, ETH-USD), and forex pairs (USDINR=X, EURUSD=X) using public market APIs "
        "with yfinance fallback. Actions: status, quote, stock, index, crypto, forex, watchlist."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | quote | stock | index | crypto | forex | watchlist (default: status)",
            },
            "symbol": {
                "type": "STRING",
                "description": "Ticker or alias (e.g. NVDA, AAPL, nifty, sp500, btc, eth, usdinr, eurusd).",
            },
            "symbols": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "List of symbols for action='watchlist'.",
            },
        },
        "required": [],
    },
}

SYMBOL_ALIASES: dict[str, str] = {
    "sp500": "^GSPC",
    "s&p500": "^GSPC",
    "nasdaq": "^IXIC",
    "dow": "^DJI",
    "nifty": "^NSEI",
    "nifty50": "^NSEI",
    "sensex": "^BSESN",
    "btc": "BTC-USD",
    "bitcoin": "BTC-USD",
    "eth": "ETH-USD",
    "ethereum": "ETH-USD",
    "sol": "SOL-USD",
    "usdinr": "USDINR=X",
    "eurusd": "EURUSD=X",
    "gbpusd": "GBPUSD=X",
    "gold": "GC=F",
}


def resolve_symbol(raw: str, *, asset_kind: str = "") -> str:
    clean = str(raw or "").strip()
    if not clean:
        return ""
    low = clean.lower().replace(" ", "")
    if low in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[low]
    up = clean.upper()
    if asset_kind == "crypto" and "-" not in up:
        return f"{up}-USD"
    if asset_kind == "forex" and "=" not in up and len(up) == 6:
        return f"{up}=X"
    return up


def fetch_quote(raw_symbol: str, *, asset_kind: str = "") -> dict:
    """Fetch price snapshot via Yahoo public chart REST API, falling back to yfinance."""
    sym = resolve_symbol(raw_symbol, asset_kind=asset_kind)
    if not sym:
        raise ValueError("Empty ticker symbol.")

    http_err: Exception | None = None
    try:
        import httpx
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        with httpx.Client(timeout=6.0, headers=headers) as client:
            r = client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=2d&interval=1d")
            r.raise_for_status()
            meta = (((r.json().get("chart") or {}).get("result") or [{}])[0]).get("meta") or {}
            price = float(meta["regularMarketPrice"])
            prev = float(meta["chartPreviousClose"]) if meta.get("chartPreviousClose") else None
            pct = ((price - prev) / prev * 100.0) if (prev and prev > 0) else None
            return {
                "symbol": sym,
                "price": price,
                "prev_close": prev,
                "change_pct": pct,
                "currency": str(meta.get("currency") or "USD"),
                "source": "yahoo_chart",
            }
    except Exception as exc:
        http_err = exc

    try:
        import yfinance as yf
        t = yf.Ticker(sym)
        fi = t.fast_info
        price = float(fi["last_price"]) if fi.get("last_price") is not None else None
        prev = float(fi["previous_close"]) if fi.get("previous_close") is not None else None
        currency = str(fi.get("currency") or "USD")
        if price is not None:
            pct = ((price - prev) / prev * 100.0) if (prev and prev > 0) else None
            return {"symbol": sym, "price": price, "prev_close": prev, "change_pct": pct, "currency": currency, "source": "yfinance"}
    except Exception as yf_exc:
        raise RuntimeError(f"Market fetch failed ({http_err}; {yf_exc})") from yf_exc

    raise RuntimeError(f"No price data available for {sym}: {http_err}")


def format_quote(q: dict) -> str:
    pct = q.get("change_pct")
    pct_s = f" ({pct:+.2f}%)" if pct is not None else ""
    return f"{q['symbol']}: {q['currency']} {q['price']:,.2f}{pct_s} [{q['source']}]"


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for financial quotes."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    if action == "status":
        httpx_ok = True
        try:
            import httpx  # noqa: F401
        except ImportError:
            httpx_ok = False
        return f"Market Finance ready (http_chart={'ok' if httpx_ok else 'missing'}, aliases={len(SYMBOL_ALIASES)})."

    if action == "watchlist":
        syms = params.get("symbols") or ["NVDA", "^GSPC", "BTC-USD", "USDINR=X"]
        lines = []
        for s in syms[:10]:
            try:
                lines.append("- " + format_quote(fetch_quote(str(s))))
            except Exception as exc:
                lines.append(f"- {s}: unavailable ({exc})")
        return "Market Watchlist:\n" + "\n".join(lines)

    if action in ("quote", "stock", "index", "crypto", "forex"):
        sym = str(params.get("symbol") or "").strip()
        if not sym:
            return f"Provide symbol='...' for {action}."
        try:
            q = fetch_quote(sym, asset_kind="" if action == "quote" else action)
            return format_quote(q)
        except Exception as exc:
            return f"Could not fetch quote for '{sym}': {exc}"

    return f"Unknown market_finance action '{action}'. Use: status, quote, stock, index, crypto, forex, watchlist."
