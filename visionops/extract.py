"""Turn an uploaded document into text: Tesseract OCR for images, text layer for PDFs."""

from __future__ import annotations

import io

from .config import ServingConfig
from .errors import PermanentJobError

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def extract_text(data: bytes, ext: str, cfg: ServingConfig) -> str:
    ext = ext.lower()
    if ext not in cfg.allowed_extensions:
        raise PermanentJobError("unsupported_type", f"extension {ext or '(none)'} is not allowed")
    if not data:
        raise PermanentJobError("empty_file")
    if ext == ".txt":
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PermanentJobError("bad_encoding", str(exc)) from exc
    if ext == ".pdf":
        return _pdf_text(data)
    return _ocr_image(data, cfg)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:10])
    except (PdfReadError, ValueError, KeyError) as exc:
        raise PermanentJobError("corrupt_pdf", str(exc)) from exc
    if not text.strip():
        raise PermanentJobError("no_text_layer",
                                "scanned PDFs without a text layer are not supported; upload page images")
    return text


def _ocr_image(data: bytes, cfg: ServingConfig) -> str:
    import pytesseract
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise PermanentJobError("corrupt_image", str(exc)) from exc

    img = ImageOps.exif_transpose(img).convert("L")
    longest = max(img.size)
    if longest > cfg.max_image_side:
        scale = cfg.max_image_side / longest
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    img = ImageOps.autocontrast(img)
    # Tesseract failures (missing binary, crash) are not the document's fault -> transient error, retried.
    return pytesseract.image_to_string(img, lang=cfg.ocr_lang, config=f"--psm {cfg.ocr_psm}")
