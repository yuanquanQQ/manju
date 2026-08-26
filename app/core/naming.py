"""ASCII-safe pinyin naming for user-facing resource packages."""

from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import datetime

from pypinyin import Style, lazy_pinyin

_NON_NAME = re.compile(r"[^a-z0-9]+")


def pinyin_slug(value: str, *, fallback: str = "ziyuan") -> str:
    """Convert Chinese or mixed text to lowercase pinyin and digits."""

    pieces = lazy_pinyin(
        str(value or "").strip(),
        style=Style.NORMAL,
        neutral_tone_with_five=False,
        errors="default",
    )
    raw = "_".join(piece.strip().lower() for piece in pieces if piece.strip())
    normalized = unicodedata.normalize("NFKD", raw).encode(
        "ascii", "ignore"
    ).decode("ascii")
    clean = _NON_NAME.sub("_", normalized).strip("_")
    return clean or fallback


def numeric_run_id(now: datetime | None = None) -> str:
    """Return a sortable timestamp plus numeric collision suffix."""

    moment = now or datetime.now()
    return f"{moment:%Y%m%d_%H%M%S}_{secrets.randbelow(1_000_000):06d}"
