"""shared fixtures + env setup for tests.

We intentionally set stub env vars here so tests never hit real AWS.
Any test that wants a real Bedrock call should either be marked
`@pytest.mark.integration` or set the env vars itself before importing.
"""

from __future__ import annotations

import os


os.environ.setdefault("JWT_SECRET", "test-secret-32-bytes-abcdefghijkl")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("JWT_ISSUER", "compliance-rag")
os.environ.setdefault("JWT_AUDIENCE", "member-services")
os.environ.setdefault("PSEUDONYM_SALT", "00" * 32)
os.environ.setdefault("PSEUDONYM_SALT_ROTATION_DAYS", "30")
os.environ.setdefault("AUDIT_DB_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("USE_LOCAL_EMBEDDINGS", "true")
