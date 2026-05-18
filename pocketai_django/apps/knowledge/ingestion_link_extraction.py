from __future__ import annotations

import re

from bs4 import BeautifulSoup

from apps.accounts.models import KnowledgeBlockType
from apps.knowledge.documents import DocumentScrapeError, scrape_document_source
from apps.knowledge.ingestion_contracts import (
    ExtractionResult,
    KnowledgeIngestionError,
    PageBlockPayload,
    PageLayout,
)


class IngestionLinkExtractionMixin:


    def _extract_from_link(self, url: str) -> ExtractionResult:
        """
        Fetch link via scrape_document_source and return plain text extraction.
        (Keeps signature consistent with _extract_upload() caller.)
        """
        try:
            scraped = scrape_document_source(url=url, timeout=15.0, max_bytes=2_000_000)
        except DocumentScrapeError as exc:
            raise KnowledgeIngestionError(str(exc)) from exc

        text = scraped.text or ""
        content_type = (scraped.content_type or "").lower()
        fmt = "text/html" if "html" in content_type else "text/plain"

        page = PageLayout(
            page_number=1,
            width=612,
            height=792,
            rotation=0,
            text_density=len(text.strip()) / float(612 * 792),
            has_ocr_content=False,
            content_type=fmt,
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text=text,
                )
            ],
            metadata={"url": scraped.final_url},
        )

        return ExtractionResult(
            text=text,
            format_hint=fmt,
            metadata={
                "url": scraped.final_url,
                "status_code": scraped.status_code,
                "content_type": scraped.content_type,
                "word_count": scraped.word_count,
            },
            pages=[page],
            tables=[],
            issues=[],
        )

    def _sanitize_html(self, soup: BeautifulSoup, allowed_tags: list[str]) -> str:
        """
        Sanitize HTML keeping only allowed semantic tags
        Removes all attributes except href for links
        """
        # Remove disallowed tags but keep their content
        for tag in soup.find_all():
            if tag.name not in allowed_tags:
                tag.unwrap()
        
        # Clean attributes (keep only href for links)
        for tag in soup.find_all():
            if tag.name == "a" and tag.has_attr("href"):
                href = tag["href"]
                tag.attrs = {"href": href}
            else:
                tag.attrs = {}
        
        # Convert to string and clean up
        html = str(soup)
        
        # Remove excessive whitespace
        html = re.sub(r"\n\s*\n", "\n\n", html)
        html = re.sub(r" +", " ", html)
        
        return html.strip()

    def _detect_encoding(self, content_bytes: bytes) -> str:
        """Detect content encoding from bytes"""
        # Try UTF-8 first (most common)
        try:
            content_bytes.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            pass
        
        # Try common encodings
        for encoding in ["latin-1", "iso-8859-1", "windows-1252"]:
            try:
                content_bytes.decode(encoding)
                return encoding
            except UnicodeDecodeError:
                continue
        
        # Fallback
        return "utf-8"
