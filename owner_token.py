"""Deterministic opaque owner token for private podcast storage.

Avoids leaking PII (email addresses) in R2 object keys and log output.
Uses SHA-256 of the lowercased, trimmed email, first 16 hex chars.

The same algorithm is implemented in JavaScript in
admin/src/systemd.js (ownerToken / ownerTokenSync).
"""

import hashlib


def owner_token(email: str) -> str:
    """Derive a deterministic opaque token from an email address."""
    normalized = email.lower().strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
