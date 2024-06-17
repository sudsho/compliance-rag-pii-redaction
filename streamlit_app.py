"""streamlit chat UI with citation popovers and redaction preview.

Talks to the FastAPI backend at API_BASE. JWT for the demo user is
minted via the same JWT_SECRET (dev-only convenience).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests
import streamlit as st
from jose import jwt


API_BASE = os.environ.get("API_BASE", "http://localhost:8080")


st.set_page_config(page_title="compliance rag demo", layout="wide")


def _mint_token(user, roles, org, consent):
    secret = os.environ.get("JWT_SECRET", "test-secret-32-bytes-abcdefghijkl")
    now = datetime.now(tz=timezone.utc)
    return jwt.encode(
        {
            "sub": user, "roles": roles, "org_id": org,
            "consented_patient_ids": consent,
            "iss": os.environ.get("JWT_ISSUER", "compliance-rag"),
            "aud": os.environ.get("JWT_AUDIENCE", "member-services"),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=30)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )


st.title("compliance-aware policy chat")
st.caption("HIPAA-scoped RAG demo. All PII in sample docs is synthetic.")

with st.sidebar:
    st.subheader("caller identity (jwt claims)")
    user = st.text_input("user id", "demo-user")
    org = st.text_input("org", "acme-payer")
    roles = st.multiselect(
        "roles",
        ["nurse", "case_manager", "reviewer", "pharmacist", "member_services", "auditor", "admin"],
        default=["nurse"],
    )
    consent_raw = st.text_input("consented patient ids (comma separated)", "")
    consent = [c.strip() for c in consent_raw.split(",") if c.strip()]
    top_k = st.slider("top_k", 1, 15, 6)
    st.divider()
    show_redaction = st.checkbox("show redaction preview", value=True)


if "history" not in st.session_state:
    st.session_state.history = []


for turn in st.session_state.history:
    with st.chat_message(turn["role"]):
        st.write(turn["content"])
        if turn.get("citations"):
            with st.expander("citations"):
                for c in turn["citations"]:
                    st.write(f"[{c['source_id']}:p{c['page']}#{c['chunk_index']}]")


q = st.chat_input("ask a policy question")
if q:
    st.session_state.history.append({"role": "user", "content": q})
    with st.chat_message("user"):
        st.write(q)

    token = _mint_token(user, roles, org, consent)
    try:
        r = requests.post(
            f"{API_BASE}/ask",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": q, "top_k": top_k},
            timeout=30,
        )
    except requests.RequestException as e:
        with st.chat_message("assistant"):
            st.error(f"api error: {e}")
        st.stop()

    if r.status_code != 200:
        with st.chat_message("assistant"):
            st.error(f"http {r.status_code}: {r.text}")
        st.stop()

    body = r.json()
    with st.chat_message("assistant"):
        st.write(body["answer"])
        cols = st.columns([3, 1])
        with cols[0]:
            if body.get("citations"):
                with st.expander("citations"):
                    for c in body["citations"]:
                        st.write(f"[{c['source_id']}:p{c['page']}#{c['chunk_index']}]")
        with cols[1]:
            st.metric("guardrail (prompt)", body["guardrail_prompt"])
            st.metric("guardrail (response)", body["guardrail_response"])
            st.metric("pii entities redacted", body["redaction_n"])

    if show_redaction:
        st.divider()
        st.subheader("redaction preview (what the LLM actually saw)")
        prev = requests.post(
            f"{API_BASE}/redact/preview",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": q},
            timeout=15,
        )
        if prev.ok:
            data = prev.json()
            st.code(data["redacted_text"], language="text")
            counts = data["stats"].get("by_type", {})
            if counts:
                st.write("entities detected:")
                st.dataframe(
                    {"entity": list(counts.keys()), "count": list(counts.values())},
                    use_container_width=True,
                )
        else:
            st.warning("redaction preview endpoint unavailable")

    st.session_state.history.append(
        {"role": "assistant", "content": body["answer"], "citations": body.get("citations", [])}
    )
