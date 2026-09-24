"""Local filesystem storage adapter.

Default for development. Signed URLs are HMAC tokens verified by our own
download endpoint — that way the interface contract (signed_url) works
identically to GCS during dev, so calling code doesn't diverge.
"""
import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import BinaryIO, Optional
from app.storage.base import StoredObject
from app.core.config import settings


class LocalFilesystemStorage:
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _full_path(self, key: str) -> Path:
        # Never allow '..' in keys — defense against traversal.
        if ".." in key.split("/"):
            raise ValueError(f"Invalid storage key: {key}")
        return self.root / key

    def put(self, key: str, data: BinaryIO, mime_type: str,
            metadata: Optional[dict] = None) -> StoredObject:
        full = self._full_path(key)
        full.parent.mkdir(parents=True, exist_ok=True)

        hasher = hashlib.sha256()
        size = 0
        with open(full, "wb") as f:
            while chunk := data.read(65536):
                hasher.update(chunk)
                f.write(chunk)
                size += len(chunk)

        return StoredObject(
            key=key, size_bytes=size,
            sha256_checksum=hasher.hexdigest(),
            mime_type=mime_type,
        )

    def get(self, key: str) -> BinaryIO:
        full = self._full_path(key)
        if not full.exists():
            raise FileNotFoundError(key)
        return open(full, "rb")

    def signed_url(self, key: str, expires_in: int = 300) -> str:
        """HMAC-signed token our /files download endpoint verifies.
        Returned path is relative — callers prepend API base."""
        expires_at = int(time.time()) + expires_in
        payload = f"{key}:{expires_at}"
        sig = hmac.new(
            settings.SECRET_KEY.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        # url-safe key encoding (base64 the key so slashes are preserved as
        # opaque path parameters, not real path segments)
        import base64
        encoded = base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")
        return f"/api/v1/files/signed/{encoded}?expires={expires_at}&sig={sig}"

    @staticmethod
    def verify_signature(encoded_key: str, expires: int, sig: str) -> Optional[str]:
        """Verify a signed URL and return the storage key, or None if invalid."""
        if time.time() > expires:
            return None
        import base64
        try:
            padding = "=" * (-len(encoded_key) % 4)
            key = base64.urlsafe_b64decode(encoded_key + padding).decode()
        except Exception:
            return None
        expected = hmac.new(
            settings.SECRET_KEY.encode(),
            f"{key}:{expires}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return key if hmac.compare_digest(expected, sig) else None

    def delete(self, key: str) -> None:
        full = self._full_path(key)
        if full.exists():
            full.unlink()

    def exists(self, key: str) -> bool:
        return self._full_path(key).exists()
