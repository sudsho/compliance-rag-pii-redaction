"""jwt claims -> CallerIdentity.

We do NOT trust unverified tokens. All decode uses jose with the
configured secret + algorithm + issuer + audience.
"""

from __future__ import annotations

import os

from jose import JWTError, jwt

from src.retrieve import CallerIdentity


class AuthError(Exception):
    pass


def decode_jwt(token: str) -> dict:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise AuthError("server missing JWT_SECRET")
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=[os.environ.get("JWT_ALGORITHM", "HS256")],
            issuer=os.environ.get("JWT_ISSUER", "compliance-rag"),
            audience=os.environ.get("JWT_AUDIENCE", "member-services"),
        )
    except JWTError as e:
        raise AuthError(str(e)) from e


def identity_from_claims(claims: dict) -> CallerIdentity:
    roles = tuple(claims.get("roles") or [])
    org_id = claims.get("org_id") or ""
    consented = tuple(claims.get("consented_patient_ids") or [])
    user_id = claims.get("sub") or claims.get("user_id") or "?"
    return CallerIdentity(
        user_id=user_id,
        roles=roles,
        org_id=org_id,
        consented_patient_ids=consented,
    )


def identity_from_token(token: str) -> CallerIdentity:
    return identity_from_claims(decode_jwt(token))
