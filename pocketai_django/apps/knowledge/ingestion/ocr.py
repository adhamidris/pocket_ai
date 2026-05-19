from __future__ import annotations

import logging
from typing import Any, Callable

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import IssuePayload


logger = logging.getLogger(__name__)


def create_tesseract_ocr_callable() -> Callable[[bytes], str] | None:
    """
    Create Tesseract OCR callable for low-density PDF pages
    Returns None if Tesseract is not available
    """
    try:
        import pytesseract
        from PIL import Image
        import io
        
        def ocr_callable(image_bytes: bytes) -> str:
            """OCR callable that takes image bytes and returns text"""
            try:
                image = Image.open(io.BytesIO(image_bytes))
                text = pytesseract.image_to_string(
                    image,
                    config='--psm 1'
                )
                return text.strip()
            except Exception as exc:
                logger.warning(f"Tesseract OCR failed: {exc}")
                return ""
        
        try:
            pytesseract.get_tesseract_version()
            logger.info("Tesseract OCR enabled successfully")
            return ocr_callable
        except Exception:
            logger.warning("Tesseract not found - OCR will be disabled")
            return None
            
    except ImportError:
        logger.warning("pytesseract not installed - pip install pytesseract")
        return None


def create_ocr_reconciler(
    *,
    density_threshold: float = 0.00015,
    render_dpi: int = 200,
    enable_ocr: bool = True,
) -> OCRReconciler:
    """
    Factory function to create OCRReconciler with optional Tesseract support
    """
    ocr_callable = None
    
    if enable_ocr:
        ocr_callable = create_tesseract_ocr_callable()
        if ocr_callable:
            logger.info("OCR enabled with density threshold: %.6f", density_threshold)
        else:
            logger.warning("OCR requested but not available")
    
    return OCRReconciler(
        density_threshold=density_threshold,
        render_dpi=render_dpi,
        ocr_callable=ocr_callable,
    )


class OCRReconciler:
    """
    Lightweight OCR orchestrator that flags low-density pages and optionally runs OCR.
    """

    def __init__(
        self,
        *,
        density_threshold: float = 0.00015,
        render_dpi: int = 200,
        ocr_callable: Callable[[bytes], str] | None = None,
    ):
        self.density_threshold = density_threshold
        try:
            dpi = int(render_dpi)
        except (TypeError, ValueError):
            dpi = 200
        self.render_dpi = max(72, min(600, dpi))
        self.ocr_callable = ocr_callable

    def reconcile_pdf_page(
        self,
        page: Any,
        *,
        page_number: int,
        extracted_text: str,
        text_density: float,
    ) -> tuple[str, bool, list[IssuePayload]]:
        if text_density >= self.density_threshold:
            return extracted_text, False, []

        issues: list[IssuePayload] = []
        if self.ocr_callable is None:
            issues.append(
                IssuePayload(
                    code="ocr_required",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description="Page text density is low but OCR is not configured.",
                    page_number=page_number,
                    details={"text_density": text_density},
                )
            )
            return extracted_text, False, issues

        try:
            try:
                pixmap = page.get_pixmap(dpi=self.render_dpi, alpha=False)  # type: ignore[attr-defined]
            except TypeError:
                pixmap = page.get_pixmap(alpha=False)  # type: ignore[attr-defined]
            image_bytes = pixmap.tobytes("png")
            ocr_text = self.ocr_callable(image_bytes)
            if not ocr_text:
                issues.append(
                    IssuePayload(
                        code="ocr_empty",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description="OCR returned no text for low-density page.",
                        page_number=page_number,
                    )
                )
                return extracted_text, False, issues
            return ocr_text, True, []
        except Exception as exc:  # pragma: no cover - best effort
            issues.append(
                IssuePayload(
                    code="ocr_failed",
                    severity=KnowledgeIssueSeverity.ERROR.value,
                    description=f"OCR failed for page {page_number}: {exc}",
                    page_number=page_number,
                )
            )
            return extracted_text, False, issues
