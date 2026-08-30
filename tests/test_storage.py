"""Storage adapter tests."""
import io
import time
import tempfile
from app.storage.local import LocalFilesystemStorage
from app.storage.factory import build_storage_key, get_storage


def test_local_put_get_roundtrip(tmp_path):
    s = LocalFilesystemStorage(str(tmp_path))
    payload = b"hello world"
    obj = s.put("test/path/file.txt", io.BytesIO(payload), "text/plain")
    assert obj.size_bytes == len(payload)
    assert obj.sha256_checksum == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    stream = s.get("test/path/file.txt")
    assert stream.read() == payload
    assert s.exists("test/path/file.txt") is True


def test_local_rejects_path_traversal(tmp_path):
    s = LocalFilesystemStorage(str(tmp_path))
    import pytest
    with pytest.raises(ValueError):
        s.put("../escape.txt", io.BytesIO(b"x"), "text/plain")


def test_signed_url_verify_roundtrip():
    key = "companies/1/datasets/ds_abc/versions/v1/df_xyz_data.xlsx"
    from app.storage.local import LocalFilesystemStorage as L
    from app.storage.factory import get_storage as _gs
    _gs.cache_clear()
    from app.core.config import settings
    with tempfile.TemporaryDirectory() as td:
        settings.STORAGE_LOCAL_ROOT = td
        settings.STORAGE_BACKEND = "local"
        s = LocalFilesystemStorage(td)
        url = s.signed_url(key, expires_in=60)
        # Parse the URL back
        import re
        m = re.search(r"/files/signed/([^?]+)\?expires=(\d+)&sig=([0-9a-f]+)", url)
        assert m
        encoded, expires, sig = m.group(1), int(m.group(2)), m.group(3)
        # Valid sig -> returns key
        result = L.verify_signature(encoded, expires, sig)
        assert result == key


def test_signed_url_rejects_tampered_sig():
    from app.storage.local import LocalFilesystemStorage as L
    from app.core.config import settings
    with tempfile.TemporaryDirectory() as td:
        settings.STORAGE_LOCAL_ROOT = td
        s = LocalFilesystemStorage(td)
        url = s.signed_url("some/key.xlsx", expires_in=60)
        import re
        m = re.search(r"/files/signed/([^?]+)\?expires=(\d+)&sig=([0-9a-f]+)", url)
        encoded, expires, sig = m.group(1), int(m.group(2)), m.group(3)
        # Tamper
        bad_sig = "0" * len(sig)
        assert L.verify_signature(encoded, expires, bad_sig) is None


def test_signed_url_expires():
    from app.storage.local import LocalFilesystemStorage as L
    from app.core.config import settings
    with tempfile.TemporaryDirectory() as td:
        settings.STORAGE_LOCAL_ROOT = td
        s = LocalFilesystemStorage(td)
        url = s.signed_url("some/key.xlsx", expires_in=-1)  # already expired
        import re
        m = re.search(r"/files/signed/([^?]+)\?expires=(\d+)&sig=([0-9a-f]+)", url)
        encoded, expires, sig = m.group(1), int(m.group(2)), m.group(3)
        assert L.verify_signature(encoded, expires, sig) is None


def test_build_storage_key_sanitizes_filename():
    key = build_storage_key(
        company_id=42, dataset_public_id="ds_abc",
        version_number=1, file_public_id="df_xyz",
        filename="../../evil name.xlsx",
    )
    # No path traversal, no whitespace
    assert ".." not in key.split("/")
    assert " " not in key
    assert "companies/42/" in key
    assert key.endswith("df_xyz_.._.._evil_name.xlsx")
    # The ".." characters are inside a single filename segment (after df_xyz_ prefix), # not a directory separator, so no traversal is possible.


def test_storage_backend_bad_setting_raises():
    from app.storage.factory import get_storage as _gs
    _gs.cache_clear()
    from app.core.config import settings
    settings.STORAGE_BACKEND = "nonexistent"
    import pytest
    with pytest.raises(RuntimeError):
        _gs()
    # reset
    settings.STORAGE_BACKEND = "local"
    _gs.cache_clear()
