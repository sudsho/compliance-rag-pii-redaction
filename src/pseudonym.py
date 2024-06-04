"""hash-based reversible pseudonymization with rotating salt.

Design goal: the same real value produces the same pseudonym for the
lifetime of a salt epoch, so downstream analytics can still join
records for the same patient. Salt rotates every N days; on rotation
the old salt is retained so historical records remain resolvable via
a keyed reverse index (stored server side, encrypted at rest).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class PseudonymConfig:
    salt: bytes
    epoch: date
    rotation_days: int


def load_config_from_env() -> PseudonymConfig:
    salt_hex = os.environ.get("PSEUDONYM_SALT", "")
    if not salt_hex or len(salt_hex) < 32:
        raise RuntimeError("PSEUDONYM_SALT must be >=32 hex chars, refuse to run insecure")
    rotation = int(os.environ.get("PSEUDONYM_SALT_ROTATION_DAYS", "30"))
    return PseudonymConfig(
        salt=bytes.fromhex(salt_hex),
        epoch=_current_epoch(rotation),
        rotation_days=rotation,
    )


def _current_epoch(rotation_days: int) -> date:
    # anchor at unix epoch, snap to nearest rotation boundary
    today = datetime.now(tz=timezone.utc).date()
    ordinal = today.toordinal()
    snapped = ordinal - (ordinal % rotation_days)
    return date.fromordinal(snapped)


def pseudonym(value: str, entity: str, cfg: PseudonymConfig) -> str:
    """Deterministic pseudonym for (value, entity) under a salt epoch.

    Format: <ENTITY_TAG>_<10-char-b32>
    """
    epoch_bytes = cfg.epoch.isoformat().encode("ascii")
    msg = b"|".join([entity.encode("ascii"), value.encode("utf-8"), epoch_bytes])
    digest = hmac.new(cfg.salt, msg, hashlib.sha256).digest()
    token = base64.b32encode(digest)[:10].decode("ascii")
    return f"{entity}_{token}"


def rotate_now(cfg: PseudonymConfig) -> PseudonymConfig:
    """Force a new epoch boundary from today (used on manual key rotation)."""
    return PseudonymConfig(
        salt=cfg.salt, epoch=datetime.now(tz=timezone.utc).date(), rotation_days=cfg.rotation_days
    )
