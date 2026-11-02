from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str
    used_ocr: bool = False


def extract_text(path: Path, mime_type: str | None = None, tesseract_cmd: str | None = None) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf" or mime_type == "application/pdf":
        return extract_pdf_text(path)
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"} or (
        mime_type and mime_type.startswith("image/")
    ):
        return extract_image_text(path, tesseract_cmd)
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_pdf_text(path: Path) -> str:
    pages = extract_pdf_pages(path)
    return "\n\n".join(
        f"[Page {page.page_number}]\n{page.text}"
        for page in pages
        if page.text.strip()
    ).strip()


def extract_pdf_pages(path: Path, tesseract_cmd: str | None = None) -> list[PageText]:
    import pdfplumber

    pages: list[PageText] = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            used_ocr = False
            if not text.strip():
                text = _ocr_pdf_page(page, tesseract_cmd)
                used_ocr = bool(text.strip())
            pages.append(
                PageText(
                    page_number=page_number,
                    text=text.strip(),
                    used_ocr=used_ocr,
                )
            )
    return pages


def extract_pages(path: Path, tesseract_cmd: str | None = None) -> list[PageText]:
    if path.suffix.lower() == ".pdf":
        return extract_pdf_pages(path, tesseract_cmd)
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    return [PageText(page_number=1, text=text)] if text else []


def _ocr_pdf_page(page, tesseract_cmd: str | None = None) -> str:
    try:
        import pytesseract

        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        image = page.to_image(resolution=200).original
        return pytesseract.image_to_string(image).strip()
    except Exception:
        return ""


def extract_image_text(path: Path, tesseract_cmd: str | None = None) -> str:
    try:
        from PIL import Image
        import pytesseract

        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        with Image.open(path) as image:
            return pytesseract.image_to_string(image).strip()
    except Exception as tesseract_error:
        try:
            from paddleocr import PaddleOCR

            ocr = PaddleOCR(use_angle_cls=True, lang="en")
            result = ocr.ocr(str(path), cls=True)
            lines: list[str] = []
            for block in result or []:
                for item in block or []:
                    if len(item) >= 2 and item[1]:
                        lines.append(str(item[1][0]))
            return "\n".join(lines).strip()
        except Exception:
            raise RuntimeError(
                "Image OCR failed. Install Tesseract OCR and keep tesseract.exe on PATH, "
                "or set TESSERACT_CMD in .env. "
                f"Original error: {tesseract_error}"
            ) from tesseract_error
