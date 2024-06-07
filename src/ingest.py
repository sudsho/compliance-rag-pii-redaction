"""document ingestion: pdf, docx, markdown -> chunked with page provenance.

Chunks carry (source_id, page, chunk_index) so citations resolve back to
a specific page of a specific policy document. This matters because the
generator is not allowed to make claims without a chunk id in the answer.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable


@dataclass
class Chunk:
    id: str
    text: str
    source_id: str
    source_path: str
    page: int
    chunk_index: int
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _sha_id(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:16]


def _split_paragraphs(text: str, target: int = 800, overlap: int = 120) -> list[str]:
    """Simple paragraph packer with overlap. Good enough for policies where
    paragraphs are the natural chunk unit."""
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for p in paras:
        if buf_len + len(p) > target and buf:
            chunks.append("\n\n".join(buf))
            # overlap: keep tail of last chunk
            tail = chunks[-1][-overlap:] if overlap else ""
            buf = [tail, p] if tail else [p]
            buf_len = sum(len(x) for x in buf)
        else:
            buf.append(p)
            buf_len += len(p)
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def load_pdf(path: Path) -> list[tuple[int, str]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    out = []
    for i, page in enumerate(reader.pages, start=1):
        out.append((i, page.extract_text() or ""))
    return out


def load_docx(path: Path) -> list[tuple[int, str]]:
    import docx  # type: ignore[import-untyped]

    d = docx.Document(str(path))
    # docx has no pages in the source; emit as page 1
    full = "\n".join(p.text for p in d.paragraphs)
    return [(1, full)]


def load_markdown(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    return [(1, text)]


def load_any(path: Path) -> list[tuple[int, str]]:
    suf = path.suffix.lower()
    if suf == ".pdf":
        return load_pdf(path)
    if suf == ".docx":
        return load_docx(path)
    if suf in {".md", ".markdown", ".txt"}:
        return load_markdown(path)
    raise ValueError(f"unsupported file type: {suf}")


def ingest_file(
    path: Path,
    source_id: str | None = None,
    metadata: dict | None = None,
    chunk_size: int = 800,
    overlap: int = 120,
) -> list[Chunk]:
    src_id = source_id or path.stem
    md = metadata or {}
    pages = load_any(path)
    out: list[Chunk] = []
    for page_num, page_text in pages:
        if not page_text.strip():
            continue
        for i, ch in enumerate(_split_paragraphs(page_text, chunk_size, overlap)):
            cid = _sha_id(src_id, str(page_num), str(i), ch[:32])
            out.append(
                Chunk(
                    id=cid,
                    text=ch,
                    source_id=src_id,
                    source_path=str(path),
                    page=page_num,
                    chunk_index=i,
                    metadata=md,
                )
            )
    return out


def ingest_dir(
    root: Path,
    patterns: Iterable[str] = ("*.pdf", "*.docx", "*.md"),
    metadata_by_source: dict[str, dict] | None = None,
) -> list[Chunk]:
    """Recursively ingest a directory. Per-source metadata (roles, org_id,
    patient_ids allowed to see this doc) can be attached by source_id."""
    all_chunks: list[Chunk] = []
    for pat in patterns:
        for p in sorted(root.rglob(pat)):
            src_id = p.stem
            md = (metadata_by_source or {}).get(src_id, {})
            all_chunks.extend(ingest_file(p, source_id=src_id, metadata=md))
    return all_chunks


def flatten_provenance(chunks: list[Chunk]) -> list[dict]:
    """A citation-friendly view of the chunks: (source, page, chunk_index)."""
    return [
        {
            "chunk_id": c.id,
            "source_id": c.source_id,
            "page": c.page,
            "chunk_index": c.chunk_index,
        }
        for c in chunks
    ]
