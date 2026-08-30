"""Short URL-safe public IDs for external API exposure.

Per approved architecture: keep integer PKs internally (unchanged from Stage 3),
but expose UUID-based public IDs on API surfaces. This gives us:
- non-enumerable URLs (no /datasets/1, /datasets/2 leaking counts)
- collision-safe identifiers when master data eventually merges from multiple sources
- ability to swap internal storage without changing external contracts
"""
import secrets
import string

# 22 chars, URL-safe, ~131 bits of entropy — more than a UUID4, more compact.
_ALPHABET = string.ascii_letters + string.digits


def generate_public_id(prefix: str = "") -> str:
    """Generate a short URL-safe ID. Optional prefix helps humans identify type
    in logs (e.g. 'ds_' for datasets, 'st_' for sites)."""
    body = ''.join(secrets.choice(_ALPHABET) for _ in range(22))
    return f"{prefix}{body}" if prefix else body
