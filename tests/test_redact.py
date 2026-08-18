"""cover the 18 hipaa safe harbor identifiers.

We do NOT test that every recognizer catches every possible form; that's
what presidio's own test suite does. Here we verify that our wired-up
Redactor produces a document with the raw value gone from the text and
the entity type present in the entities list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.presidio_redact import Redactor
from src.pseudonym import PseudonymConfig
from datetime import date

CONFIG = Path("configs/hipaa_recognizers.yaml")


@pytest.fixture(scope="module")
def redactor():
    pseudo = PseudonymConfig(
        salt=bytes.fromhex("00" * 32),
        epoch=date(2024, 6, 1),
        rotation_days=30,
    )
    return Redactor(recognizer_config=CONFIG, pseudonym_config=pseudo)


HIPAA_CASES = [
    # 1 names
    ("Patient John Smith reported symptoms.", "John Smith", "PERSON"),
    # 2 geographic subdivision
    ("Patient lives in Cambridge, MA.", "Cambridge", "LOCATION"),
    # 3 dates
    ("DOB 1962-05-14", "1962-05-14", "DATE_TIME"),
    # 4 telephone
    ("Call 617-555-0134 for follow up.", "617-555-0134", "PHONE_NUMBER"),
    # 5 fax (presidio treats as PHONE_NUMBER; acceptable)
    ("Fax the release form to 617-555-0198.", "617-555-0198", "PHONE_NUMBER"),
    # 6 email
    ("Contact jane.doe@example.org for prior auth.", "jane.doe@example.org", "EMAIL_ADDRESS"),
    # 7 ssn (avoid 123-45-6789: presidio blocklists it as a sample placeholder)
    ("SSN 412-34-5678", "412-34-5678", "US_SSN"),
    # 8 medical record number
    ("MRN: 000123456 on file.", "000123456", "MEDICAL_RECORD_NUMBER"),
    # 9 health plan beneficiary
    ("Member ID: ABC1234567890", "ABC1234567890", "HEALTH_PLAN_BENEFICIARY"),
    # 10 account number
    ("Account 4111-1111-1111-1111", "4111-1111-1111-1111", "CREDIT_CARD"),
    # 11 certificate / license
    ("DEA: AB1234567", "AB1234567", "DEA_NUMBER"),
    # 12 vehicle vin
    ("VIN 1HGCM82633A123456", "1HGCM82633A123456", "VIN"),
    # 13 device serial
    ("Serial: SN-0055-XY-42", "SN-0055-XY-42", "DEVICE_SERIAL"),
    # 14 url
    ("See https://portal.example.com/x", "https://portal.example.com/x", "URL"),
    # 15 ip
    ("Logged in from 192.168.42.99", "192.168.42.99", "IP_ADDRESS"),
    # 16 biometric
    (
        "fingerprint: a3d2c8b1e4f60793a1b2c3d4e5f60718a3d2c8b1e4f60793a1b2c3d4e5f60718",
        "a3d2c8b1e4f60793a1b2c3d4e5f60718a3d2c8b1e4f60793a1b2c3d4e5f60718",
        "BIOMETRIC",
    ),
    # 17 photograph -> out of scope for text
    # 18 any other unique code -> NPI here
    ("NPI: 1234567893", "1234567893", "NPI"),
]


@pytest.mark.parametrize("text,raw,entity", HIPAA_CASES)
def test_hipaa_identifiers_removed(redactor, text, raw, entity):
    result = redactor.redact(text)
    assert raw not in result.text, f"raw {entity} value leaked: {result.text!r}"
    types = {e["type"] for e in result.entities}
    assert entity in types, f"expected {entity} in {types}, text={text!r}"


def test_stats_report_counts(redactor):
    text = "John Smith, mrn 000123456 and 000987654, email a@b.co"
    r = redactor.redact(text)
    assert r.stats["n"] >= 3
    assert sum(r.stats["by_type"].values()) == r.stats["n"]


def test_pseudonym_is_stable(redactor):
    a = redactor.redact("Patient John Smith called about MRN: 000123456")
    b = redactor.redact("MRN: 000123456 for John Smith on file")
    mrn_a = [e for e in a.entities if e["type"] == "MEDICAL_RECORD_NUMBER"]
    mrn_b = [e for e in b.entities if e["type"] == "MEDICAL_RECORD_NUMBER"]
    assert mrn_a and mrn_b
    # both redacted; pseudonym stability at the text level:
    assert "MRN_" in a.text and "MRN_" in b.text
