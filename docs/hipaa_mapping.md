# HIPAA control mapping

This document maps HIPAA Privacy + Security Rule controls to the specific
code path in this repo that implements them. It is intended as an aid to
a HIPAA readiness assessment, not a substitute for one; treat it as a
technical starting point for a Security Officer's review.

## Safe Harbor de-identification (45 CFR 164.514(b)(2))

Each of the 18 identifiers must be removed for a dataset to qualify as
de-identified under the Safe Harbor method.

| # | Identifier | Where it is handled | How |
| --- | --- | --- | --- |
| 1 | Names | `src/presidio_redact.py` + Presidio `PERSON` recognizer | pseudonymized to `PERSON_<hash10>` |
| 2 | Geographic subdivisions < state | `src/presidio_redact.py` + Presidio `LOCATION` | replaced with `<LOCATION>` |
| 3 | Dates (except year) | `src/presidio_redact.py` + Presidio `DATE_TIME` | replaced with `<DATE>` (or date-shifted) |
| 4 | Telephone | Presidio `PHONE_NUMBER` | pseudonymized to `PHONE_<hash10>` |
| 5 | Fax | Presidio `PHONE_NUMBER` (same regex family) | pseudonymized |
| 6 | Email | Presidio `EMAIL_ADDRESS` | pseudonymized to `EMAIL_<hash10>` |
| 7 | SSN | Presidio `US_SSN` | last 5 masked with `*` |
| 8 | Medical record number | Custom recognizer `MRN` in `configs/hipaa_recognizers.yaml` | pseudonymized to `MRN_<hash10>` |
| 9 | Health plan beneficiary number | Custom recognizer `HEALTH_PLAN_ID` | pseudonymized to `PLAN_<hash10>` |
| 10 | Account numbers | Presidio `CREDIT_CARD` + custom `HEALTH_PLAN_ID` | masked / pseudonymized |
| 11 | Certificate / license (DEA, NPI, DL) | Custom `DEA_NUMBER`, `NPI` + Presidio `US_DRIVER_LICENSE` | pseudonymized |
| 12 | Vehicle identifiers | Custom `VEHICLE_VIN` | replaced |
| 13 | Device identifiers | Custom `DEVICE_SERIAL` | replaced |
| 14 | Web URLs | Presidio `URL` | replaced with `<URL>` |
| 15 | IP addresses | Presidio `IP_ADDRESS` | replaced with `<IP>` |
| 16 | Biometric | Custom `BIOMETRIC` | replaced |
| 17 | Full-face photographs | out of scope (text-only pipeline) | n/a |
| 18 | Any other unique code | catch-all `DEFAULT` operator | replaced with `<REDACTED>` |

## Security Rule controls

| Control | Reference | Where handled |
| --- | --- | --- |
| Access control - unique user id | 164.312(a)(2)(i) | JWT `sub` claim, `src/identity.py`, propagated to audit log |
| Access control - emergency access | 164.312(a)(2)(ii) | out of scope for chat surface; documented in `docs/on_prem_deploy.md` |
| Access control - automatic logoff | 164.312(a)(2)(iii) | JWT expiry (`exp`), enforced in `src/identity.py::decode_jwt` |
| Access control - encryption / decryption | 164.312(a)(2)(iv) | RDS KMS at rest (`terraform/rds.tf`), S3 KMS (`terraform/s3.tf`) |
| Audit controls | 164.312(b) | `src/audit.py` hash-chained log, per-request row, tamper-evident |
| Integrity - authenticate ePHI | 164.312(c)(2) | hash chain: any mutation invalidates subsequent `row_hash`, verified by `AuditLog.verify_chain()` |
| Person or entity authentication | 164.312(d) | JWT signature verification |
| Transmission security - integrity | 164.312(e)(2)(i) | TLS on all client -> API + API -> AWS calls; Bedrock via VPC endpoint (`terraform/vpc.tf`) |
| Transmission security - encryption | 164.312(e)(2)(ii) | TLS via VPC private endpoints; no public internet egress for model traffic |

## Privacy Rule controls

| Control | Reference | Where handled |
| --- | --- | --- |
| Minimum necessary | 164.502(b) | Retrieval-time ACL filter in `src/retrieve.py`; per-role, per-org, per-patient-consent scoping |
| Uses and disclosures - consent | 164.506 | `consented_patient_ids` claim in JWT; enforced in `_patient_filter` |
| Individual access | 164.524 | out of scope for internal-only assistant; documented as future work |
| Accounting of disclosures | 164.528 | audit log rows include `user_id`, `retrieved_chunk_ids`, `citations`; queryable via `/audit` endpoint |

## Breach Notification Rule

The audit log is the primary evidentiary artifact for breach analysis.
Because the chain is tamper-evident, a partial log tail can still be
independently verified even if downstream systems are compromised. See
`docs/audit_log_design.md`.

## What this does NOT do

- No BAA management; assumes AWS Bedrock, Amazon RDS, and Amazon S3
  BAAs are already in place at the organizational level.
- No key management workflows beyond KMS with rotation enabled;
  organizational HSM policies are outside scope.
- No user-facing Notice of Privacy Practices flow (this is a staff
  tool, not a member-facing app).
