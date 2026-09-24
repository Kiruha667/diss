"""Shared paths, constants and I/O helpers for the whole pipeline.

Unified trajectory record (one JSON object per line in
``<INTERIM>/<source>/trajectories.jsonl.gz``) — the schema from AGENTS.md E0, extended
with a few fields that are never used as model features::

    {
      "run_id":   str,   # "<submission>/<task_id>", unique
      "task_id":  str,   # SWE-bench instance_id
      "agent_id": str,   # scaffold + version, e.g. "mini-swe-agent-1.13.3"
      "model_id": str,   # e.g. "claude-sonnet-4-5-20250929"
      "model_family": str,  # provider family, for Test 6 (e.g. "anthropic")
      "source":   str,   # adapter name, e.g. "swebench_bashonly"
      "label":    int,   # 1 = failure, 0 = success (from the test harness)
      "task_text": str,  # task statement only (problem statement)
      "steps": [ {"role": "assistant"|"tool"|"user", "text": str, "step_idx": int,
                  "tool_calls": [str, ...]} ],
      "meta":     {...}  # bookkeeping only (exit status etc.) — NEVER a feature
    }

Conventions:
* ``step_idx`` = 0-based index of the agent step; one assistant message opens a step, the
  tool output(s) answering it carry the same ``step_idx``.
* Assistant ``text`` = visible reasoning + visible content; the executed commands are
  appended as fenced ```bash blocks, so v1 (inline) and v2 (tool-call) formats look the same
  to the segmenter. ``tool_calls`` holds the raw command strings (for n_tool_calls /
  repeat_ratio). Tool/user steps have ``tool_calls == []``.
* System prompt and the initial instruction message are not steps (the task statement is in
  ``task_text``). Harness feedback (format errors etc.) is ``role="user"``.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import random
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

# Every location can be redirected (Kaggle writes under /kaggle/working, raw data under /kaggle/tmp).
DATA = Path(os.environ.get("DISS_DATA", ROOT / "data"))
RAW = Path(os.environ.get("DISS_RAW", DATA / "raw"))
INTERIM = DATA / "interim"
EMB = Path(os.environ.get("DISS_EMB", DATA / "emb"))
OUTPUTS = Path(os.environ.get("DISS_OUTPUTS", ROOT / "outputs"))
REPORTS = Path(os.environ.get("DISS_REPORTS", ROOT / "reports"))
CONFIG = ROOT / "config"

SEED = 42
SEGS = ("reason_sent", "all_sent", "step")
PROJS = ("cos_step", "cos_task", "pca1", "norm")
N_GRID = (50, 100, 200, 400)  # E3 grid {100,200,400} + N=50 for Test 5
D_GRID = (3, 4)
MAX_N = max(N_GRID)  # only the first MAX_N fragments are ever embedded or featurised
N_FOLDS = 5

ENCODERS = {
    "minilm": {"name": "sentence-transformers/all-MiniLM-L6-v2", "prefix": "", "trust_remote_code": False,
               "max_seq_length": 256},
    # nomic needs a task prefix; "clustering: " is the documented one for topic/semantic structure.
    # Its native context is 8192 tokens; fragments are sentences / code lines (steps for seg=step), so input is
    # truncated at 512 tokens — otherwise a batch of long step fragments exhausts a 16 GB GPU.
    "nomic": {"name": "nomic-ai/nomic-embed-text-v1.5", "prefix": "clustering: ", "trust_remote_code": True,
              "max_seq_length": 512},
}

FEATURE_COLUMNS = [
    "run_id", "task_id", "agent_id", "model_id", "label",
    "seg", "proj", "N", "d",
    "H", "C", "fisher",
    "n_steps", "n_frag", "total_chars", "mean_frag_len", "n_tool_calls", "repeat_ratio",
]
BASE_FEATURES = ["n_steps", "n_frag", "total_chars", "mean_frag_len", "n_tool_calls", "repeat_ratio"]
CHAOS_FEATURES = ["H", "C"]


def seed_everything(seed: int = SEED) -> None:
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
    except ImportError:
        pass


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return logging.getLogger(name)


def safe_name(run_id: str) -> str:
    """File-system safe version of a run_id ("sub/task" -> "sub__task")."""
    return run_id.replace("/", "__").replace("\\", "__")


def interim_dir(source: str) -> Path:
    return INTERIM / source


def trajectories_path(source: str) -> Path:
    return interim_dir(source) / "trajectories.jsonl.gz"


def segments_path(source: str, seg: str) -> Path:
    return interim_dir(source) / f"segments_{seg}.jsonl.gz"


def emb_path(encoder: str, seg: str, run_id: str) -> Path:
    # AGENTS.md: data/emb/{seg}/{run_id}.npy for the main encoder; the control encoder gets its own root.
    base = EMB if encoder == "minilm" else EMB.parent / f"emb_{encoder}"
    return base / seg / f"{safe_name(run_id)}.npy"


def task_emb_path(encoder: str, task_id: str) -> Path:
    base = EMB if encoder == "minilm" else EMB.parent / f"emb_{encoder}"
    return base / "task" / f"{safe_name(task_id)}.npy"


def write_jsonl_gz(path: Path, records: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    tmp.replace(path)
    return n


def read_jsonl_gz(path: Path) -> Iterator[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def load_leak_markers(path: Path | None = None) -> list[str]:
    path = path or CONFIG / "leak_markers.txt"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out
