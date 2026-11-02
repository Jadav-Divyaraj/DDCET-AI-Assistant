from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
PRESENTATION_SUFFIXES = {".ppt", ".pptx", ".pps", ".ppsx"}
WORD_SUFFIXES = {".doc", ".docx"}
OFFICE_SUFFIXES = PRESENTATION_SUFFIXES | WORD_SUFFIXES


@dataclass(frozen=True)
class RenderedOfficeDocument:
    pdf_path: Path
    page_images: list[Path]
    page_kind: str


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")
    return (cleaned or "upload")[:120]


def is_supported_image(path: Path, mime_type: str | None = None) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES or bool(
        mime_type and mime_type.startswith("image/")
    )


def prepare_image_for_vision(
    source: Path,
    destination: Path,
    *,
    max_dimension: int = 1800,
    jpeg_quality: int = 86,
) -> Path:
    from PIL import Image, ImageOps

    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((max_dimension, max_dimension))
        image.save(destination, "JPEG", quality=jpeg_quality, optimize=True)
    return destination


def render_pdf_pages(
    source: Path,
    output_dir: Path,
    *,
    limit: int = 3,
    resolution: int = 150,
) -> list[Path]:
    import pdfplumber

    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    with pdfplumber.open(source) as pdf:
        for index, page in enumerate(pdf.pages[: max(1, min(limit, 3))], start=1):
            destination = output_dir / f"page-{index}.jpg"
            image = page.to_image(resolution=resolution).original.convert("RGB")
            image.thumbnail((1800, 1800))
            image.save(destination, "JPEG", quality=86, optimize=True)
            rendered.append(destination)
    return rendered


def render_office_document_pages(
    source: Path,
    output_dir: Path,
    *,
    limit: int = 3,
) -> RenderedOfficeDocument:
    suffix = source.suffix.lower()
    if suffix not in OFFICE_SUFFIXES:
        raise ValueError(f"Unsupported Office document type: {suffix or 'unknown'}")

    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{source.stem}.pdf"
    if sys.platform == "win32" and _microsoft_office_available(suffix):
        _convert_with_microsoft_office(source, pdf_path, suffix)
    else:
        _convert_with_libreoffice(source, pdf_path)

    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise OSError(f"Document conversion did not create a readable PDF: {source.name}")

    page_images = render_pdf_pages(
        pdf_path,
        output_dir / "rendered",
        limit=limit,
    )
    return RenderedOfficeDocument(
        pdf_path=pdf_path,
        page_images=page_images,
        page_kind="slides" if suffix in PRESENTATION_SUFFIXES else "pages",
    )


def _microsoft_office_available(suffix: str) -> bool:
    executable = "POWERPNT.EXE" if suffix in PRESENTATION_SUFFIXES else "WINWORD.EXE"
    candidates = (
        Path("C:/Program Files/Microsoft Office/root/Office16") / executable,
        Path("C:/Program Files (x86)/Microsoft Office/root/Office16") / executable,
    )
    return any(candidate.is_file() for candidate in candidates)


def _convert_with_microsoft_office(
    source: Path,
    pdf_path: Path,
    suffix: str,
) -> None:
    if suffix in PRESENTATION_SUFFIXES:
        script = _POWERPOINT_TO_PDF_SCRIPT
    else:
        script = _WORD_TO_PDF_SCRIPT
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& {{ {script} }}",
            str(source.resolve()),
            str(pdf_path.resolve()),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "conversion failed").strip()
        raise OSError(f"Microsoft Office conversion failed: {detail[:500]}")


def _convert_with_libreoffice(source: Path, pdf_path: Path) -> None:
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if not executable:
        raise OSError(
            "PowerPoint/Word rendering requires Microsoft Office on Windows or "
            "LibreOffice on other systems."
        )
    completed = subprocess.run(
        [
            executable,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pdf_path.parent),
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    generated = pdf_path.parent / f"{source.stem}.pdf"
    if completed.returncode != 0 or not generated.is_file():
        detail = (completed.stderr or completed.stdout or "conversion failed").strip()
        raise OSError(f"LibreOffice conversion failed: {detail[:500]}")
    if generated.resolve() != pdf_path.resolve():
        generated.replace(pdf_path)


_POWERPOINT_TO_PDF_SCRIPT = r"""
param([string]$sourceFile, [string]$destinationFile)
$ErrorActionPreference = 'Stop'
$inputPath = [System.IO.Path]::GetFullPath($sourceFile)
$outputPath = [System.IO.Path]::GetFullPath($destinationFile)
$application = $null
$presentation = $null
try {
    $application = New-Object -ComObject PowerPoint.Application
    $presentation = $application.Presentations.Open($inputPath, $true, $true, $false)
    $presentation.SaveAs($outputPath, 32)
}
finally {
    if ($null -ne $presentation) {
        try { $presentation.Close() } catch {}
        try {
            [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject(
                $presentation
            )
        } catch {}
    }
    if ($null -ne $application) {
        try { $application.Quit() } catch {}
        try {
            [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject(
                $application
            )
        } catch {}
    }
}
"""


_WORD_TO_PDF_SCRIPT = r"""
param([string]$sourceFile, [string]$destinationFile)
$ErrorActionPreference = 'Stop'
$inputPath = [System.IO.Path]::GetFullPath($sourceFile)
$outputPath = [System.IO.Path]::GetFullPath($destinationFile)
$application = $null
$document = $null
try {
    $application = New-Object -ComObject Word.Application
    $application.Visible = $false
    $document = $application.Documents.Open($inputPath, $false, $true)
    $document.ExportAsFixedFormat($outputPath, 17)
}
finally {
    if ($null -ne $document) {
        try { $document.Close($false) } catch {}
        try {
            [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject(
                $document
            )
        } catch {}
    }
    if ($null -ne $application) {
        try { $application.Quit() } catch {}
        try {
            [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject(
                $application
            )
        } catch {}
    }
}
"""
