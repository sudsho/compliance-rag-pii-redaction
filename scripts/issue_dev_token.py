"""issue a dev jwt for local testing.

Never use in prod. Signing key comes from JWT_SECRET env var; if that is
not set we refuse to run so nobody generates a token with a default
secret and forgets about it.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone

from jose import jwt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default="u1")
    ap.add_argument("--org", default="acme-payer")
    ap.add_argument("--roles", nargs="+", default=["nurse"])
    ap.add_argument("--consent", nargs="*", default=[])
    ap.add_argument("--ttl-minutes", type=int, default=60)
    args = ap.parse_args()

    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise SystemExit("JWT_SECRET must be set")

    now = datetime.now(tz=timezone.utc)
    claims = {
        "sub": args.user_id,
        "roles": args.roles,
        "org_id": args.org,
        "consented_patient_ids": args.consent,
        "iss": os.environ.get("JWT_ISSUER", "compliance-rag"),
        "aud": os.environ.get("JWT_AUDIENCE", "member-services"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=args.ttl_minutes)).timestamp()),
    }
    tok = jwt.encode(claims, secret, algorithm=os.environ.get("JWT_ALGORITHM", "HS256"))
    print(tok)


if __name__ == "__main__":
    main()
