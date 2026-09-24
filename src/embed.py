"""E1: embedding of segmented fragments, with an on-disk cache (data/emb/{seg}/{run_id}.npy, float32).

Only the first MAX_N fragments of each trajectory are stored in the segment files, so nothing beyond the
largest analysed window is ever embedded. Existing .npy files are skipped, so interrupted runs resume.

Embeddings are the encoders' standard outputs (unit-normalised). Consequence worth knowing: for unit
vectors ||a-b|| = sqrt(2 * (1 - cos(a,b))), so proj=norm is a monotone transform of proj=cos_step and the
two give identical ordinal-pattern features (reported in E2).
"""
from __future__ import annotations

import numpy as np

from src.common import ENCODERS, emb_path, get_logger, read_jsonl_gz, segments_path, task_emb_path

log = get_logger("embed")


def load_encoder(encoder: str = "minilm", device: str | None = None):
    from sentence_transformers import SentenceTransformer

    spec = ENCODERS[encoder]
    model = SentenceTransformer(spec["name"], device=device, trust_remote_code=spec["trust_remote_code"])
    model.max_seq_length = spec["max_seq_length"]
    return model


def encoder_revision(encoder: str) -> str:
    """HF commit of the encoder actually used (recorded in reports for reproducibility)."""
    try:
        from huggingface_hub import model_info

        return model_info(ENCODERS[encoder]["name"]).sha or "unknown"
    except Exception as e:  # offline etc. — informational only
        return f"unknown ({type(e).__name__})"


def _encode(model, texts: list[str], encoder: str, batch_size: int) -> np.ndarray:
    prefix = ENCODERS[encoder]["prefix"]
    if prefix:
        texts = [prefix + t for t in texts]
    emb = model.encode(texts, batch_size=batch_size, convert_to_numpy=True, normalize_embeddings=True,
                       show_progress_bar=False)
    return emb.astype(np.float32)


def _save(path, arr: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.npy")
    np.save(tmp, arr)
    tmp.replace(path)


def embed_segments(source: str, seg: str, encoder: str = "minilm", run_ids: set[str] | None = None,
                   batch_size: int = 256, chunk_fragments: int = 50_000, device: str | None = None,
                   model=None) -> dict:
    """Embed the stored fragments of every (selected) trajectory; skip those already cached.

    Fragments of many trajectories are pooled into chunks of ~``chunk_fragments`` for throughput and
    split back per trajectory afterwards.
    """
    model = model or load_encoder(encoder, device)
    done = todo = 0
    buf_ids: list[tuple[str, int]] = []
    buf_txt: list[str] = []

    def flush():
        nonlocal buf_ids, buf_txt
        if not buf_txt:
            return
        emb = _encode(model, buf_txt, encoder, batch_size)
        i = 0
        for rid, n in buf_ids:
            _save(emb_path(encoder, seg, rid), emb[i:i + n])
            i += n
        buf_ids, buf_txt = [], []

    for rec in read_jsonl_gz(segments_path(source, seg)):
        rid = rec["run_id"]
        if run_ids is not None and rid not in run_ids:
            continue
        if emb_path(encoder, seg, rid).exists():
            done += 1
            continue
        texts = [f["t"] for f in rec["frags"]]
        if not texts:
            continue
        buf_ids.append((rid, len(texts)))
        buf_txt.extend(texts)
        todo += 1
        if len(buf_txt) >= chunk_fragments:
            flush()
            log.info("%s/%s/%s: embedded %d trajectories so far", encoder, seg, source, todo)
    flush()
    log.info("%s/%s/%s: %d embedded now, %d were cached", encoder, seg, source, todo, done)
    return {"embedded": todo, "cached": done}


def embed_tasks(task_texts: dict[str, list[str]], encoder: str = "minilm", batch_size: int = 256,
                device: str | None = None, model=None) -> int:
    """Task-statement embedding = unit-normalised mean of its fragment embeddings (for proj=cos_task)."""
    model = model or load_encoder(encoder, device)
    n = 0
    for tid, sents in task_texts.items():
        p = task_emb_path(encoder, tid)
        if p.exists() or not sents:
            continue
        e = _encode(model, sents, encoder, batch_size).mean(axis=0)
        _save(p, (e / max(np.linalg.norm(e), 1e-12)).astype(np.float32))
        n += 1
    return n
