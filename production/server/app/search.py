"""Web search via DuckDuckGo only.

Google scraping is not used (Google blocks scrapers) — this is the
rule from the fixed spec. Two DuckDuckGo routes, in order:

1. Instant Answer API (api.duckduckgo.com) — official JSON endpoint,
   abstract + related topics with source URLs. Reliable, but thin:
   it returns nothing for weather/news/product queries.
2. DuckDuckGo Lite HTML endpoint, parsed with BeautifulSoup.

Every result carries its source (title, URL, publisher) so replies
can name and link it. If both routes fail or return nothing, the
caller raises — the chat endpoint then says so honestly instead of
inventing an answer.

Known limit: DuckDuckGo challenges datacenter IPs. From this dev
sandbox the Lite endpoint times out and HTML routes return challenge
pages; on Render it may behave the same. If search stays blocked in
production, the honest fix is a paid search API (e.g. Brave Search
API or Tavily) behind the same interface — the chat code does not
need to change, only this module.
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Aries/0.4; +https://aries.app)"}


def search_web(query: str, max_results: int = 5) -> list[dict]:
    """Return [{title, url, snippet, publisher}] for a query."""
    query = (query or "").strip()
    if not query:
        return []
    results = _search_instant_answer(query, max_results)
    if results:
        return results
    results = _search_lite(query, max_results)
    if results:
        return results
    raise RuntimeError(
        "DuckDuckGo returned no results (it may be blocking this "
        "server's IP). Try again later."
    )


def _publisher(url: str) -> str:
    try:
        host = url.split("://", 1)[-1].split("/", 1)[0]
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _search_instant_answer(query: str, max_results: int) -> list[dict]:
    """api.duckduckgo.com — abstract + related topics."""
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    out: list[dict] = []
    abstract = (data.get("AbstractText") or "").strip()
    if abstract:
        out.append(
            {
                "title": data.get("Heading") or query,
                "url": data.get("AbstractURL") or "",
                "snippet": abstract,
                "publisher": _publisher(data.get("AbstractURL") or ""),
            }
        )
    for topic in data.get("RelatedTopics") or []:
        if len(out) >= max_results:
            break
        if isinstance(topic, dict) and topic.get("Text") and topic.get("FirstURL"):
            out.append(
                {
                    "title": topic["Text"].split(" - ")[0][:120],
                    "url": topic["FirstURL"],
                    "snippet": topic["Text"],
                    "publisher": _publisher(topic["FirstURL"]),
                }
            )
    return out


def _search_lite(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo Lite HTML endpoint (POST, parsed with BeautifulSoup)."""
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for block in soup.select(".result")[:max_results]:
        link = block.select_one(".result__a")
        snippet = block.select_one(".result__snippet")
        if not link:
            continue
        url = link.get("href", "")
        out.append(
            {
                "title": link.get_text(strip=True) or url,
                "url": url,
                "snippet": snippet.get_text(strip=True) if snippet else "",
                "publisher": _publisher(url),
            }
        )
    return out


def looks_like_current_question(text: str) -> bool:
    """Cheap heuristic: does this question need fresh info?"""
    lowered = text.lower()
    triggers = (
        "today", "yesterday", "this week", "latest", "news", "price",
        "how much", "weather", "score", "release", "when is", "when does",
        "current", "now", "2026", "2027",
    )
    return any(t in lowered for t in triggers)
