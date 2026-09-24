"""Run the heavy stages on Kaggle (internet + GPU) from this machine, via the Kaggle CLI.

    python -m src.kaggle_job push   --stage e0 --user <kaggle_username>
    python -m src.kaggle_job status --stage e0 --user <kaggle_username>
    python -m src.kaggle_job pull   --stage e0 --user <kaggle_username>

Each stage is a private Kaggle script kernel "diss-<stage>" whose code file embeds this repository's src/,
config/ and requirements.txt (base64 tar), so the cloud run uses exactly the local code.
  e0  CPU+internet: download the 16 runs (~7.5 GB, to /tmp), E0 inventory, E1 segmentation (all segs)
  e1  GPU+internet: input = e0 output; E1 pilot (200 trajectories) -> acceptance check -> full MiniLM
      embedding of all segs -> nomic control encoder on the 300-trajectory subsample
  e3  CPU: inputs = e0 + e1 outputs; E2/E3 on the pilot combo, then the full grid (MiniLM and nomic),
      then E4 statistics
Kernel outputs (/kaggle/working) keep data/interim (e0) and emb.tar (e1, all .npy caches in one file) for
the next stage; `pull` downloads only outputs/, reports/ and config/ into the repository (the embeddings
stay on Kaggle).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

from src.common import ROOT

STAGES = {
    "e0": {"gpu": False, "inputs": [], "title": "diss-e0"},
    "e1": {"gpu": True, "inputs": ["e0"], "title": "diss-e1"},
    "e3": {"gpu": False, "inputs": ["e0", "e1"], "title": "diss-e3"},
}
# Versions that determine results; the rest of the Kaggle image is logged (reports/kaggle_env_<stage>.txt).
PINNED = ["ordpy==1.2.3", "blingfire==0.1.8", "sentence-transformers==5.7.0", "transformers==4.57.6",
          "huggingface-hub==0.36.2", "einops==0.8.2", "scikit-learn==1.9.1", "statsmodels==0.15.0"]
BUILD = ROOT / "kaggle" / "build"
PULLED = ROOT / "kaggle" / "pulled"

RUNNER = r'''
import base64, glob, io, os, shutil, subprocess, sys, tarfile
STAGE = "__STAGE__"
PAYLOAD = "__PAYLOAD__"
PINNED = __PINNED__
W = "/kaggle/working"
CODE = "/tmp/diss"

def sh(cmd, env=None):
    print("$", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True, env=env, cwd=CODE)

os.makedirs(CODE, exist_ok=True)
tarfile.open(fileobj=io.BytesIO(base64.b64decode(PAYLOAD)), mode="r:gz").extractall(CODE)
subprocess.run([sys.executable, "-m", "pip", "install", "-q"] + PINNED, check=True)
for d in ("outputs", "reports", "config"):
    os.makedirs(os.path.join(W, d), exist_ok=True)

def find_input(marker):
    hits = glob.glob(f"/kaggle/input/**/{marker}", recursive=True)
    if not hits:
        sys.exit(f"input not found: {marker}")
    return hits[0][: -len(marker)].rstrip("/")

env = dict(os.environ, PYTHONUNBUFFERED="1", HF_HUB_DISABLE_PROGRESS_BARS="1",
           DISS_OUTPUTS=f"{W}/outputs", DISS_REPORTS=f"{W}/reports")

def restore(prev):
    """Copy a previous stage's small outputs back into place (outputs/, reports/, leak markers)."""
    for d in ("outputs", "reports"):
        src = os.path.join(prev, d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(W, d), dirs_exist_ok=True)
    lm = os.path.join(prev, "config", "leak_markers.txt")
    if os.path.exists(lm):
        shutil.copy(lm, os.path.join(CODE, "config", "leak_markers.txt"))
        shutil.copy(lm, os.path.join(W, "config", "leak_markers.txt"))

if STAGE == "e0":
    env.update(DISS_DATA=f"{W}/data", DISS_RAW="/tmp/raw")
    sh("python -m src.cli download --workers 32", env)
    sh("python -m src.cli inventory", env)
    sh("python -m src.cli segment", env)
    shutil.copy(os.path.join(CODE, "config", "leak_markers.txt"), os.path.join(W, "config", "leak_markers.txt"))
elif STAGE == "e1":
    e0 = find_input("data/interim/swebench_bashonly/trajectories.jsonl.gz")
    restore(e0)
    # embeddings go to /tmp (tens of thousands of .npy files) and are shipped as ONE tar in the output
    env.update(DISS_DATA=f"{e0}/data", DISS_EMB="/tmp/emb")
    import pandas as pd
    st = pd.read_csv(f"{W}/outputs/segment_stats.csv")
    med = st.loc[st.seg == "reason_sent", "n_frag_total"].median()
    print(f"E1 acceptance: median reason_sent fragments = {med}", flush=True)
    sh("python -m src.cli build --seg reason_sent --limit 200", env)
    pil = set(pd.read_csv(f"{W}/outputs/e1_pilot_runs.csv").run_id)
    pmed = st.loc[(st.seg == "reason_sent") & st.run_id.isin(pil), "n_frag_total"].median()
    print(f"E1 pilot (200 trajectories): median reason_sent fragments = {pmed}", flush=True)
    if med < 200:
        sys.exit("E1 acceptance NOT met (median < 200): stop; re-run `segment --clauses` (see AGENTS.md E1)")
    sh("python -m src.cli build", env)
    # ship the main embeddings BEFORE the control encoder runs, so a control failure cannot lose them
    sh(f"tar -cf {W}/emb.tar -C /tmp emb", env)
    try:
        sh("python -m src.cli build --encoder nomic --batch-size 64", dict(env, PYTORCH_ALLOC_CONF="expandable_segments:True"))
        sh(f"tar -cf {W}/emb_nomic.tar -C /tmp emb_nomic", env)
    except subprocess.CalledProcessError as e:
        print(f"WARNING: control encoder (nomic) failed: {e}; main embeddings are saved", flush=True)
elif STAGE == "e3":
    e0 = find_input("data/interim/swebench_bashonly/trajectories.jsonl.gz")
    e1 = find_input("emb.tar")
    restore(e0)
    restore(e1)
    sh(f"tar -xf {e1}/emb.tar -C /tmp", env)
    has_nomic = os.path.exists(f"{e1}/emb_nomic.tar")
    if has_nomic:
        sh(f"tar -xf {e1}/emb_nomic.tar -C /tmp", env)
    env.update(DISS_DATA=f"{e0}/data", DISS_EMB="/tmp/emb")
    sh("python -m src.cli features --seg reason_sent --proj cos_step --N 200 --d 4", env)
    sh("python -m src.cli features", env)
    sh("python -m src.cli stats --all", env)
    if has_nomic:
        try:
            sh("python -m src.cli features --encoder nomic", env)
            sh("python -m src.cli stats --all --encoder nomic", env)
            sh("python -m src.cli encoder-check", env)
        except subprocess.CalledProcessError as e:
            print(f"WARNING: control-encoder analysis failed: {e}", flush=True)
with open(os.path.join(W, "reports", f"kaggle_env_{STAGE}.txt"), "w") as f:
    f.write(subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True).stdout)
print("STAGE DONE", STAGE, flush=True)
'''


def _payload() -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted((ROOT / "src").rglob("*.py")):
            tf.add(p, arcname=str(p.relative_to(ROOT)).replace("\\", "/"))
        for name in ("datasets.yaml", "leak_marker_candidates.txt"):
            tf.add(ROOT / "config" / name, arcname=f"config/{name}")
        tf.add(ROOT / "requirements.txt", arcname="requirements.txt")
    return base64.b64encode(buf.getvalue()).decode()


def _kaggle(*args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("kaggle") or str(Path.home() / ".local" / "bin" / "kaggle.exe")
    return subprocess.run([exe, *args], text=True, capture_output=True)


def push(stage: str, user: str) -> None:
    cfg = STAGES[stage]
    d = BUILD / stage
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    code = (RUNNER.replace("__STAGE__", stage).replace("__PINNED__", json.dumps(PINNED))
            .replace("__PAYLOAD__", _payload()))
    (d / "run.py").write_text(code, encoding="utf-8")
    meta = {
        "id": f"{user}/{cfg['title']}", "title": cfg["title"], "code_file": "run.py", "language": "python",
        "kernel_type": "script", "is_private": True, "enable_gpu": cfg["gpu"], "enable_internet": True,
        "dataset_sources": [], "competition_sources": [], "model_sources": [],
        "kernel_sources": [f"{user}/{STAGES[s]['title']}" for s in cfg["inputs"]],
    }
    (d / "kernel-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    r = _kaggle("kernels", "push", "-p", str(d))
    print(r.stdout or r.stderr)


def status(stage: str, user: str) -> str:
    r = _kaggle("kernels", "status", f"{user}/{STAGES[stage]['title']}")
    out = (r.stdout or r.stderr).strip()
    print(out)
    return out


def pull(stage: str, user: str) -> None:
    dest = PULLED / stage
    dest.mkdir(parents=True, exist_ok=True)
    r = _kaggle("kernels", "output", f"{user}/{STAGES[stage]['title']}", "-p", str(dest), "-o",
                "--file-pattern", r"^(outputs|reports|config)/|\.log$")
    print(r.stdout[-2000:] if r.stdout else r.stderr)
    for sub in ("outputs", "reports", "config"):
        src = dest / sub
        if src.is_dir():
            shutil.copytree(src, ROOT / sub, dirs_exist_ok=True)
    print(f"copied outputs/, reports/, config/ from {dest} into the repository")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m src.kaggle_job")
    p.add_argument("action", choices=["push", "status", "pull"])
    p.add_argument("--stage", required=True, choices=list(STAGES))
    p.add_argument("--user", required=True, help="Kaggle username")
    a = p.parse_args(argv)
    {"push": push, "status": status, "pull": pull}[a.action](a.stage, a.user)


if __name__ == "__main__":
    main()
