"""Canonical JSON: one byte sequence per record, so hashes and signatures are stable.

Rules: UTF-8, object keys sorted, no insignificant whitespace, no NaN or
Infinity, non-ASCII text written as-is (not \\u-escaped). Numbers are rounded
before they enter a record, so their shortest round-trip form is stable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def canonical_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
