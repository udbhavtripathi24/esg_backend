"""Storage factory: selects adapter from settings.

Fails LOUDLY at startup if the wrong backend is configured — better than
failing on the first user upload.
"""
from functools import lru_cache
from app.storage.base import ObjectStorage
from app.storage.local import LocalFilesystemStorage
from app.core.config import settings


@lru_cache(maxsize=1)
def get_storage() -> ObjectStorage:
    backend = getattr(settings, "STORAGE_BACKEND", "local")
    if backend == "local":
        root = getattr(settings, "STORAGE_LOCAL_ROOT", "./storage")
        return LocalFilesystemStorage(root=root)
    elif backend == "gcs":
        from app.storage.gcs import GCSStorage
        bucket = getattr(settings, "STORAGE_BUCKET", None)
        if not bucket:
            raise RuntimeError("STORAGE_BACKEND=gcs but STORAGE_BUCKET not set")
        return GCSStorage(bucket_name=bucket)
    else:
        raise RuntimeError(f"Unknown STORAGE_BACKEND: {backend}")


def build_storage_key(company_id: int, dataset_public_id: str,
                      version_number: int, file_public_id: str,
                      filename: str) -> str:
    """Canonical key format. Company-scoped prefix is a defense-in-depth."""
    import re
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:100]
    return (f"companies/{company_id}/datasets/{dataset_public_id}"
            f"/versions/v{version_number}/{file_public_id}_{safe}")
