from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

import dateparser
import httpx
from bs4 import BeautifulSoup

from .scraper import USER_AGENT, LinkResult, clean_text, unique_links


BASE_URL = "https://webrcticomp.wixsite.com/rcticomputer"


@dataclass(frozen=True)
class AssignmentItem:
    title: str
    url: str
    due_at: datetime | None


class RctiScraper:
    async def fetch_links(self, limit: int = 20) -> list[LinkResult]:
        html = await self._get(BASE_URL)
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        results: list[LinkResult] = []
        for anchor in soup.find_all("a", href=True):
            title = clean_text(anchor.get_text(" ", strip=True)) or anchor["href"]
            href = urljoin(BASE_URL, anchor["href"])
            if not title or title.lower() in {"home", "more"}:
                continue
            results.append(LinkResult(title=title[:180], url=href, source="RCTI"))
        return unique_links(results)[:limit]

    async def fetch_assignment_items(self) -> list[AssignmentItem]:
        links = await self.fetch_links(limit=60)
        assignment_links: list[AssignmentItem] = []
        for link in links:
            haystack = f"{link.title} {link.url}".lower()
            if not any(word in haystack for word in ("assignment", "homework", "submission", "practical", "material")):
                continue
            assignment_links.append(
                AssignmentItem(title=link.title, url=link.url, due_at=parse_date_from_text(link.title))
            )
        return assignment_links

    async def _get(self, url: str) -> str:
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


def parse_date_from_text(text: str) -> datetime | None:
    matches = re.findall(
        r"\b\d{1,2}[-/ ](?:\d{1,2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[-/ ]\d{2,4}\b",
        text,
        flags=re.IGNORECASE,
    )
    for match in matches:
        parsed = dateparser.parse(match, settings={"PREFER_DATES_FROM": "future"})
        if parsed:
            return parsed
    return None

