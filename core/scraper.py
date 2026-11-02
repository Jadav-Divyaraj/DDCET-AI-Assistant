from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


USER_AGENT = "GTU-Student-Bot/1.0 (+educational project)"


@dataclass(frozen=True)
class LinkResult:
    title: str
    url: str
    source: str


class Scraper:
    def __init__(self, pdf_sources: tuple[str, ...]) -> None:
        self.pdf_sources = pdf_sources

    async def search_pdfs(self, query: str, limit: int = 8) -> list[LinkResult]:
        results: list[LinkResult] = []
        for source in self.pdf_sources:
            for url in candidate_urls(source, query):
                html = await self._get_text(url)
                if not html:
                    continue
                results.extend(extract_pdf_links(html, url, source, query))
                if len(results) >= limit:
                    return unique_links(results)[:limit]
        return unique_links(results)[:limit]

    async def search_web(self, query: str, limit: int = 5) -> list[LinkResult]:
        url = "https://html.duckduckgo.com/html/"
        data = {"q": query}
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"}
        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20) as client:
                response = await client.post(url, data=data)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                results: list[LinkResult] = []
                for a in soup.find_all("a", class_="result__snippet"):
                    title = clean_text(a.get_text(" ", strip=True))
                    href = a.get("href")
                    if href and title:
                        results.append(LinkResult(title=title, url=href, source="DuckDuckGo"))
                return unique_links(results)[:limit]
        except Exception:
            return []

    async def fetch_image(self, query: str) -> str | None:
        """Fetches an image URL for the given query using Wikipedia."""
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "prop": "pageimages",
            "format": "json",
            "piprop": "thumbnail",
            "pithumbsize": "1000",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": 1
        }
        headers = {"User-Agent": "DDCETBot/1.0"}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=15) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                pages = data.get("query", {}).get("pages", {})
                for page_id, page_data in pages.items():
                    original = page_data.get("thumbnail", {})
                    source = original.get("source")
                    if source:
                        return source
        except Exception as exc:
            LOGGER.warning(f"Image search failed for '{query}': {exc}")
        return None

    async def fetch_gtu_news(self, limit: int = 10) -> list[LinkResult]:
        pages = (
            "https://www.gtu.ac.in",
            "https://www.gtu.ac.in/Circular.aspx",
            "https://www.gtu.ac.in/News.aspx",
            "https://www.gtu.ac.in/Schedule.aspx",
        )
        keywords = ("exam", "schedule", "circular", "timetable", "time table", "diploma", "result", "winter", "summer")
        results: list[LinkResult] = []
        for page in pages:
            html = await self._get_text(page)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.find_all("a", href=True):
                title = clean_text(anchor.get_text(" ", strip=True)) or anchor["href"]
                href = urljoin(page, anchor["href"])
                haystack = f"{title} {href}".lower()
                if any(keyword in haystack for keyword in keywords):
                    results.append(LinkResult(title=title[:180], url=href, source="GTU"))
        return unique_links(results)[:limit]

    async def download(self, url: str) -> tuple[bytes, str]:
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=45) as client:
            response = await client.get(url)
            response.raise_for_status()
        filename = filename_from_url(url)
        return response.content, filename

    async def _get_text(self, url: str) -> str:
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=20,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.text
        except httpx.HTTPError:
            return ""


def candidate_urls(source: str, query: str) -> list[str]:
    encoded = quote_plus(query)
    return [
        source,
        f"{source}/?s={encoded}",
        f"{source}/search?q={encoded}",
        f"{source}/?q={encoded}",
    ]


def extract_pdf_links(html: str, base_url: str, source: str, query: str) -> list[LinkResult]:
    soup = BeautifulSoup(html, "html.parser")
    query_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
    results: list[LinkResult] = []
    for anchor in soup.find_all("a", href=True):
        href = unescape(anchor["href"])
        if ".pdf" not in href.lower():
            continue
        title = clean_text(anchor.get_text(" ", strip=True)) or filename_from_url(href)
        absolute = urljoin(base_url, href)
        haystack_terms = set(re.findall(r"[a-z0-9]+", f"{title} {absolute}".lower()))
        if query_terms and not query_terms.intersection(haystack_terms):
            continue
        results.append(LinkResult(title=title[:180], url=absolute, source=source))
    return results


def unique_links(results: list[LinkResult]) -> list[LinkResult]:
    seen: set[str] = set()
    unique: list[LinkResult] = []
    for item in results:
        if item.url in seen:
            continue
        seen.add(item.url)
        unique.append(item)
    return unique


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1] or "download.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)

