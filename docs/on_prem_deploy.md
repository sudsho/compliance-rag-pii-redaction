# On-prem / air-gapped deploy notes

Some payers and hospital systems refuse to send PHI anywhere near a
public cloud LLM endpoint regardless of BAA coverage. This document
describes what needs to change to run the same stack inside a private
datacenter.

## Component swap table

| Component | Cloud default | On-prem substitute |
| --- | --- | --- |
| Generation LLM | Bedrock Claude 3.5 Sonnet | vLLM or TGI serving Llama-3-70B-Instruct on 2 x H100 |
| Embeddings | Bedrock Titan v2 | sentence-transformers `intfloat/e5-large-v2` on GPU |
| Guardrails | Bedrock Guardrails | NeMo Guardrails + Llama-Guard-2 classifier |
| Vector store | ChromaDB (self-hosted still fine) | ChromaDB or pgvector on the same Postgres cluster |
| Audit DB | Amazon RDS Postgres | Enterprise Postgres cluster with WAL shipping to WORM S3-compatible storage |
| Tracing | OTLP -> Jaeger or AWS Distro for OTel | OTLP -> Grafana Tempo, on-prem Grafana |
| KMS | AWS KMS | HashiCorp Vault Transit or hardware HSM |

## Config knobs

Set in `.env` or the deployment secret manager:

```
USE_LOCAL_EMBEDDINGS=true
USE_LOCAL_LLM=true
LLM_ENDPOINT=http://vllm.internal:8000/v1
LLM_MODEL=meta-llama/Meta-Llama-3-70B-Instruct
GUARDRAIL_PROVIDER=nemo
NEMO_CONFIG_DIR=/etc/compliance-rag/nemo
```

Corresponding branches in `src/embed.py` and `src/generate.py` short-
circuit before any boto3 client is created. Bedrock never gets
initialized in on-prem mode; there are no dangling IAM roles pointing to
public AWS.

## Network posture

- API tier and vLLM serving are on the same private VLAN with mTLS.
- No egress rules to the internet from any pod running this service.
- ChromaDB and Postgres are cluster-local, TLS from CA in the corp PKI.
- Central log aggregation via rsyslog / Splunk HEC, no vendor cloud
  telemetry.

## Deployment options

1. **Kubernetes with Helm chart** (preferred).
   Manifests in `deploy/k8s/` (not yet checked in). Uses SealedSecrets
   for `.env` values and Kyverno policies to prevent any pod from
   opening a socket to a non-cluster IP.

2. **Systemd on baremetal**.
   For sites without Kubernetes. Provided the machine has NVIDIA drivers
   + CUDA 12.4 + Python 3.11, a systemd unit template lives in
   `deploy/systemd/` (also pending). vLLM + the API + Postgres each run
   as separate units, tied together with a network namespace so nothing
   listens on the wire.

## What you lose

- Bedrock Guardrails' managed content classifiers. NeMo Guardrails does
  the same thing but requires you to write and maintain the config
  files, and its topic / policy filters are less polished than Bedrock's
  managed set. Compensate with a stricter system prompt and human
  review of a sample of responses.
- Managed model updates. Anthropic will not silently upgrade the model
  version underneath you, which cuts both ways: no unexpected behavior
  changes, but also no free improvements.
- Auto-scaling on demand. Plan capacity around P95 concurrent chat
  sessions and provision GPU headroom explicitly.

## What stays the same

- The Presidio de-identification pipeline (`src/presidio_redact.py`) is
  100% local Python and needs no cloud dependency.
- The audit hash-chain logic (`src/audit.py`) is DB-agnostic.
- The ACL filter (`src/retrieve.py::_acl_where`) is independent of vector
  store choice; the Chroma-flavored predicates translate directly to
  pgvector `WHERE` clauses with a small adapter.
