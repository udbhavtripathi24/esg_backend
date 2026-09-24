"""Object storage abstraction (Stage 4).

One Protocol, multiple adapters. Local for dev, GCS-ready for prod. Both
adapters honour the same signed-URL contract so calling code never branches on
backend.

Key structure (enforced by callers, not this layer):
    companies/{company_id}/datasets/{dataset_public_id}/versions/v{n}/{file_public_id}_{safe_filename}

Company-scoped prefix is a defense-in-depth: even a bug in a query that mixes
company_ids cannot produce a valid cross-tenant storage key.
"""
from typing import Protocol, BinaryIO, Optional
from dataclasses import dataclass


@dataclass
class StoredObject:
    key: str
    size_bytes: int
    sha256_checksum: str
    mime_type: str


class ObjectStorage(Protocol):
    def put(self, key: str, data: BinaryIO, mime_type: str,
            metadata: Optional[dict] = None) -> StoredObject:
        """Store an object. Returns metadata including computed SHA-256."""
        ...

    def get(self, key: str) -> BinaryIO:
        """Return a binary stream for the object. Raises FileNotFoundError."""
        ...

    def signed_url(self, key: str, expires_in: int = 300) -> str:
        """Return a time-limited URL for direct download. Contract holds for
        LOCAL adapter too — it returns a signed token our own /files download
        endpoint verifies. Never expose the raw storage_key to clients."""
        ...

    def delete(self, key: str) -> None:
        ...

    def exists(self, key: str) -> bool:
        ...
