"""cli to force a pseudonym salt rotation and record the boundary.

Real deployment: run this via a scheduled Lambda triggered on key rotation
in AWS Secrets Manager. Boundary written to a small audit table so old
records remain resolvable via a keyed reverse index.
"""

from __future__ import annotations

import argparse
import os
import secrets
from datetime import datetime, timezone

from src.pseudonym import PseudonymConfig, load_config_from_env, rotate_now


def _new_hex_salt() -> str:
    return secrets.token_hex(32)


def _write_env(path: str, salt: str) -> None:
    lines = []
    seen = False
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if line.startswith("PSEUDONYM_SALT="):
                    lines.append(f"PSEUDONYM_SALT={salt}\n")
                    seen = True
                else:
                    lines.append(line)
    if not seen:
        lines.append(f"PSEUDONYM_SALT={salt}\n")
    with open(path, "w") as f:
        f.writelines(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--env-file", default=".env")
    args = ap.parse_args()

    old: PseudonymConfig | None = None
    try:
        old = load_config_from_env()
    except RuntimeError as e:
        print(f"no prior config: {e}")

    new_salt = _new_hex_salt()
    boundary = datetime.now(tz=timezone.utc).isoformat()
    print(f"rotation boundary: {boundary}")
    if old:
        print(f"prior epoch: {old.epoch}, rotation_days: {old.rotation_days}")
        rotated = rotate_now(old)
        print(f"new epoch anchor: {rotated.epoch}")

    if args.dry_run:
        print(f"[dry-run] would rewrite {args.env_file} with fresh salt")
        return

    _write_env(args.env_file, new_salt)
    print(f"wrote fresh salt to {args.env_file}")


if __name__ == "__main__":
    main()
