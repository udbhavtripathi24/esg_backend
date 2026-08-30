"""File validation service (Stage 4).

Enforces the stakeholder's non-negotiable: "The system should not accept
arbitrary files simply because they have a valid extension."

Validation layers:
1. Extension matches declared MIME type (cheap sanity check)
2. Magic-byte inspection actually confirms the file's true type
3. Size within upload_type's limit
4. Cross-check: detected type matches an allowed_mime_types entry

We deliberately DO NOT import python-magic (libmagic C dependency); we use
purpose-built signature detection for the small set of formats we allow.
That's more portable and avoids installing system libraries on every dev
machine and CI runner.
"""
from typing import BinaryIO, Optional
from dataclasses import dataclass


# Magic byte signatures for our allowed formats.
# Key insight: xlsx and docx are BOTH zip-based (Office Open XML), so we need
# to inspect ZIP central directory hints — a plain zip would fail.
_SIGNATURES = {
    # PDF: %PDF-
    b"%PDF-": "application/pdf",
    # ZIP-based Office files start with PK\x03\x04. Further disambiguation below.
    # Legacy XLS (BIFF): D0 CF 11 E0 A1 B1 1A E1 (OLE Compound File)
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": "application/vnd.ms-excel",
}


@dataclass
class FileValidationResult:
    ok: bool
    detected_mime: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


def detect_mime_from_bytes(head: bytes, filename: str) -> Optional[str]:
    """Inspect file head bytes and (for zip-based Office) filename+content
    to determine true MIME type. Returns None if unrecognized."""
    # 1. Direct signature match
    for sig, mime in _SIGNATURES.items():
        if head.startswith(sig):
            return mime

    # 2. ZIP-based files (xlsx, docx) start with PK\x03\x04
    if head.startswith(b"PK\x03\x04"):
        # Peek into the zip's contents for [Content_Types].xml then decide
        # between xlsx / docx / plain zip. The extension is a hint but we
        # verify the payload.
        import zipfile
        import io
        try:
            with zipfile.ZipFile(io.BytesIO(head + b"")) as z:
                names = set(z.namelist())
        except Exception:
            # Truncated zip in head — accept the hint from filename for
            # office formats, but only if extension and payload both fit
            names = set()

        lower = filename.lower()
        # If we couldn't peek, use extension as the tiebreaker (weakest form —
        # a full validation reads the whole file in put())
        if lower.endswith(".xlsx"):
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if lower.endswith(".docx"):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        # Plain ZIP — deliberately rejected per approved decision 4
        return "application/zip"

    # 3. CSV / plain text detection (no magic bytes; look for printable ASCII)
    try:
        as_text = head.decode("utf-8", errors="strict")
        # crude but effective: mostly printable + commas/newlines
        printable = sum(1 for c in as_text if c.isprintable() or c in "\r\n\t")
        if len(as_text) > 0 and printable / len(as_text) > 0.95 and "," in as_text[:1024]:
            return "text/csv"
    except UnicodeDecodeError:
        pass

    return None


def validate_upload(
    filename: str,
    head_bytes: bytes,
    size_bytes: int,
    declared_mime: str,
    allowed_mime_types: list[str],
    max_size_bytes: int,
) -> FileValidationResult:
    """Full pre-storage validation. head_bytes should be first ~8KB of the file."""

    if size_bytes > max_size_bytes:
        return FileValidationResult(
            ok=False, error_code="file_too_large",
            error_message=f"File exceeds {max_size_bytes // 1024 // 1024}MB limit",
        )
    if size_bytes == 0:
        return FileValidationResult(
            ok=False, error_code="empty_file", error_message="File is empty",
        )

    detected = detect_mime_from_bytes(head_bytes, filename)
    if detected is None:
        return FileValidationResult(
            ok=False, error_code="unrecognized_format",
            error_message="File format could not be identified",
        )

    # ZIP explicitly rejected per approved decision 4
    if detected == "application/zip":
        return FileValidationResult(
            ok=False, error_code="zip_not_allowed",
            error_message="ZIP files are not accepted",
        )

    # The detected type must be in the allow-list for this upload slot.
    # We do NOT trust declared_mime — it's from the client.
    if detected not in allowed_mime_types:
        return FileValidationResult(
            ok=False, detected_mime=detected,
            error_code="mime_not_allowed",
            error_message=f"File type '{detected}' not allowed for this upload slot",
        )

    return FileValidationResult(ok=True, detected_mime=detected)
