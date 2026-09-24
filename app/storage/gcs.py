"""GCS adapter (Stage 4). Class exists so the interface is complete; deliberately
NOT wired to GCP credentials in dev (approved decision 7). Import guarded so
`google-cloud-storage` is only required when actually using this backend.
"""
from typing import BinaryIO, Optional
from app.storage.base import StoredObject


class GCSStorage:
    def __init__(self, bucket_name: str):
        try:
            from google.cloud import storage  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "google-cloud-storage not installed. Run: pip install google-cloud-storage"
            ) from e
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    def put(self, key: str, data: BinaryIO, mime_type: str,
            metadata: Optional[dict] = None) -> StoredObject:  # pragma: no cover
        import hashlib
        hasher = hashlib.sha256()
        content = data.read()
        hasher.update(content)
        blob = self._bucket.blob(key)
        if metadata:
            blob.metadata = metadata
        blob.upload_from_string(content, content_type=mime_type)
        return StoredObject(
            key=key, size_bytes=len(content),
            sha256_checksum=hasher.hexdigest(), mime_type=mime_type,
        )

    def get(self, key: str) -> BinaryIO:  # pragma: no cover
        import io
        blob = self._bucket.blob(key)
        if not blob.exists():
            raise FileNotFoundError(key)
        return io.BytesIO(blob.download_as_bytes())

    def signed_url(self, key: str, expires_in: int = 300) -> str:  # pragma: no cover
        from datetime import timedelta
        return self._bucket.blob(key).generate_signed_url(
            expiration=timedelta(seconds=expires_in), method="GET",
        )

    def delete(self, key: str) -> None:  # pragma: no cover
        self._bucket.blob(key).delete()

    def exists(self, key: str) -> bool:  # pragma: no cover
        return self._bucket.blob(key).exists()
