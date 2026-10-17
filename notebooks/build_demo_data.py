"""Build the demo data for notebooks/demo.ipynb (DEMO_COLAB.md, steps 0-2) from the existing pipeline outputs.

Run from the repository root:  python notebooks/build_demo_data.py
Inputs (local, produced by the existing pipeline; not changed):
  data/interim/swebench_bashonly/trajectories.jsonl.gz  - E0 adapter output, 7975 runs (src/adapters/swebench_bashonly.py)
  outputs/g0_final_messages.csv                         - G0 claim detector output (has_claim per run)
Outputs (data/demo/): runs.parquet, steps.parquet, examples.json, demo_lib.zip, checks.json, SHA256SUMS.
Every value is computed by the existing code (src.e5_claims.process_record, src.g0_false_success, src.segment); the
demo_lib copies are checked against it (100 random runs, seed 42, and then all runs).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo_lib import features as dfeat  # noqa: E402
from src import e5_claims as e5  # noqa: E402
from src import g0_false_success as g0  # noqa: E402
from src.common import CONFIG, OUTPUTS, load_leak_markers, read_jsonl_gz, trajectories_path  # noqa: E402
from src.segment import compile_markers, segment  # noqa: E402

SEED = 42
OUT = ROOT / "data" / "demo"
THOUGHT_MAX, COMMAND_MAX, OUTPUT_MAX, FINAL_MAX = 500, 300, 300, 1000
_RC_RE = re.compile(r"^returncode: (-?\d+)\n?")
_REPO_RE = re.compile(r"^(.+?)__(.+)-\d+$")

STEP_SCHEMA = pa.schema([
    ("run_id", pa.string()), ("step_idx", pa.int32()), ("thought", pa.string()), ("command", pa.string()),
    ("output", pa.string()), ("returncode", pa.int32()), ("phase", pa.string()),
    ("commands", pa.list_(pa.string())), ("n_outputs", pa.int16()), ("n_nonzero_rc", pa.int16()),
    ("n_sent", pa.int32())])


def repo_of(task_id: str) -> str:
    m = _REPO_RE.match(task_id)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def record_rows(rec: dict, mre, claim) -> tuple[dict, list[dict], dict]:
    """(run row, step rows, check info) of one trajectory, all from the existing code."""
    asst = [s for s in rec["steps"] if s["role"] == "assistant"]
    assert [s["step_idx"] for s in asst] == list(range(len(asst)))
    outs: dict[int, list[str]] = {}
    for s in rec["steps"]:
        if s["role"] == "tool":
            outs.setdefault(s["step_idx"], []).append(s["text"])
    n_sent = Counter(f.step_idx for f in segment(rec, "reason_sent", mre))
    old = {k: v for k, v in e5.process_record(rec, mre).items() if not k.startswith("_")}
    for c in dfeat.BOOL_FEATURES:
        old[c] = int(old[c])
    # phases with the ORIGINAL event code (same loop as process_record)
    files: dict[str, str] = {}
    events = []
    for s in asst:
        for c in s.get("tool_calls") or []:
            for kind, target in e5.command_events(c, files):
                events.append((s["step_idx"], kind, target))
    phases = []
    for i in range(len(asst)):
        ks = {k for j, k, _ in events if j == i}
        phases.append("edit" if "edit" in ks else "verify" if "verify" in ks else "other")
    # final message: G0 rules (clean_nl + pick_final) on the assistant texts of the record
    texts = [{"full": g0.clean_nl(s["text"])} for s in asst]
    fidx, ftext = g0.pick_final(texts, "full")
    steps = []
    for s, ph in zip(asst, phases):
        o = outs.get(s["step_idx"], [])
        rcs = [int(m.group(1)) if (m := _RC_RE.match(t)) else 0 for t in o]
        cmds = list(s.get("tool_calls") or [])
        steps.append({
            "run_id": rec["run_id"], "step_idx": s["step_idx"], "thought": g0.clean_nl(s["text"])[:THOUGHT_MAX],
            "command": "\n".join(cmds)[:COMMAND_MAX], "output": "\n".join(_RC_RE.sub("", t) for t in o)[:OUTPUT_MAX],
            "returncode": (next((r for r in rcs if r != 0), 0) if o else None), "phase": ph, "commands": cmds,
            "n_outputs": len(o), "n_nonzero_rc": sum(1 for t in o if t.startswith("returncode: ")),
            "n_sent": n_sent.get(s["step_idx"], 0)})
    m = re.match(r"^mini-swe-agent-(.+)$", rec["agent_id"])
    run = {"run_id": rec["run_id"], "task_id": rec["task_id"], "repo": repo_of(rec["task_id"]),
           "model": rec["model_id"], "model_family": rec["model_family"], "agent_version": m.group(1) if m else "",
           "n_steps": len(asst), "exit_status": rec["meta"].get("exit_status", ""),
           "submitted": int(rec["meta"].get("exit_status") == "Submitted"), "claim_success": None,
           "final_message": ftext[:FINAL_MAX], "resolved": 1 - int(rec["label"]),
           "phase_seq": " ".join(dfeat.PHASE_CODES[p] for p in phases),
           **{c: old[c] for c in dfeat.FEATURES}}
    info = {"final_step_idx": -1 if fidx is None else fidx,
            "claim_recomputed": bool(g0.match_any(claim, ftext)) if fidx is not None else False,
            "final_len": len(ftext)}
    return run, steps, info


_W: dict = {}


def _init_worker() -> None:
    _W["mre"] = compile_markers(load_leak_markers())
    _W["claim"] = g0.load_patterns(CONFIG / "claim_patterns.txt")


def _work(line: str):
    return record_rows(json.loads(line), _W["mre"], _W["claim"])


def _lines():
    import gzip

    with gzip.open(trajectories_path("swebench_bashonly"), "rt", encoding="utf-8") as fh:
        yield from fh


def build(workers: int = 3) -> dict:
    from multiprocessing import Pool

    OUT.mkdir(parents=True, exist_ok=True)
    g0df = pd.read_csv(OUTPUTS / "g0_final_messages.csv", dtype={"run_id": str}).set_index("run_id")
    runs, infos = [], {}
    tmp = OUT / "steps.parquet.part"
    writer = pq.ParquetWriter(tmp, STEP_SCHEMA, compression="zstd", compression_level=9)
    buf: list[dict] = []
    pool = Pool(workers, initializer=_init_worker)
    # imap keeps the input order, so the files do not depend on the number of workers
    for k, (run, steps, info) in enumerate(pool.imap(_work, _lines(), chunksize=8), 1):
        rec = {"run_id": run["run_id"]}
        run["claim_success"] = int(bool(g0df.loc[rec["run_id"], "has_claim"]))
        runs.append(run)
        infos[rec["run_id"]] = info
        buf += steps
        if len(buf) > 50_000:
            writer.write_table(pa.Table.from_pylist(buf, schema=STEP_SCHEMA))
            buf = []
        if k % 1000 == 0:
            print(f"{k} runs", flush=True)
    pool.close()
    pool.join()
    if buf:
        writer.write_table(pa.Table.from_pylist(buf, schema=STEP_SCHEMA))
    writer.close()
    tmp.replace(OUT / "steps.parquet")
    runs_df = pd.DataFrame(runs)
    for c in ("n_steps", "submitted", "claim_success", "resolved"):
        runs_df[c] = runs_df[c].astype("int64")
    for c in dfeat.FEATURES:
        if c != "n_steps":  # n_steps is also a run column and stays int
            runs_df[c] = runs_df[c].astype("float64")
    runs_df.to_parquet(OUT / "runs.parquet", index=False, compression="zstd")

    # checks of the final-message reconstruction against the G0 output
    g = g0df.loc[runs_df.run_id]
    rec_claim = np.array([infos[r]["claim_recomputed"] for r in runs_df.run_id])
    rec_idx = np.array([infos[r]["final_step_idx"] for r in runs_df.run_id])
    rec_len = np.array([infos[r]["final_len"] for r in runs_df.run_id])
    return {"n_runs": len(runs_df),
            "final_msg_checks": {"claim_mismatch": int((rec_claim != g.has_claim.to_numpy(bool)).sum()),
                                 "final_step_idx_mismatch": int((rec_idx != g.final_step_idx.to_numpy()).sum()),
                                 "final_len_mismatch": int((rec_len != g.final_msg_len.to_numpy()).sum())}}


def check_demo_lib(n_sample: int = 100) -> dict:
    """demo_lib (from the steps table) vs the original process_record values stored in runs.parquet."""
    runs = pd.read_parquet(OUT / "runs.parquet")
    steps = pd.read_parquet(OUT / "steps.parquet", columns=["run_id", "step_idx", "phase", "commands", "n_outputs",
                                                             "n_nonzero_rc", "n_sent"])
    by_run = {r: g for r, g in steps.groupby("run_id", sort=False)}
    rng = np.random.default_rng(SEED)
    sample = sorted(rng.choice(np.sort(runs.run_id.to_numpy()), size=n_sample, replace=False))
    res = {}
    for name, ids in (("sample_100", sample), ("all_runs", list(runs.run_id))):
        ref = runs.set_index("run_id").loc[ids, dfeat.FEATURES]
        worst = dict.fromkeys(dfeat.FEATURES, 0.0)
        phase_mismatch = 0
        for r in ids:
            st = dfeat.steps_from_frame(by_run[r])
            f = dfeat.trajectory_features(st)
            for c in dfeat.FEATURES:
                worst[c] = max(worst[c], abs(float(f[c]) - float(ref.loc[r, c])))
            ph = dfeat.step_phases(st)
            phase_mismatch += int(ph != list(by_run[r].sort_values("step_idx").phase))
        res[name] = {"n": len(ids), "max_abs_diff": max(worst.values()), "per_feature": worst,
                     "runs_with_phase_mismatch": phase_mismatch}
    res["sample_run_ids"] = list(sample)
    return res


def _prefer_len(df: pd.DataFrame, lo: int = 15, hi: int = 40) -> pd.DataFrame:
    """Candidates of 15-40 steps if any, else those closest to that range."""
    inside = df[(df.n_steps >= lo) & (df.n_steps <= hi)]
    if len(inside):
        return inside
    dist = np.where(df.n_steps < lo, lo - df.n_steps, df.n_steps - hi)
    return df[dist == dist.min()]


def pick_examples() -> list[dict]:
    """Deterministic choice (seed 42) of the four example types of DEMO_COLAB.md step 2."""
    runs = pd.read_parquet(OUT / "runs.parquet").sort_values("run_id").reset_index(drop=True)
    rng = np.random.default_rng(SEED)
    sub = runs[runs.submitted == 1]
    out = []
    # 1. pair inside one task: two models, one resolved and one not, both submitted, both 15-40 steps
    ok = sub[(sub.n_steps >= 15) & (sub.n_steps <= 40)]
    tasks = sorted(t for t, g in ok.groupby("task_id")
                   if g.resolved.nunique() == 2 and
                   any(a != b for a in g[g.resolved == 1].model for b in g[g.resolved == 0].model))
    task = tasks[rng.integers(len(tasks))]
    g = ok[ok.task_id == task]
    r1 = g[g.resolved == 1].iloc[rng.integers((g.resolved == 1).sum())]
    r0c = g[(g.resolved == 0) & (g.model != r1.model)]
    r0 = r0c.iloc[rng.integers(len(r0c))]
    out += [{"type": "pair_resolved", "run_id": r1.run_id, "task_id": task},
            {"type": "pair_unresolved", "run_id": r0.run_id, "task_id": task}]
    used = {r1.run_id, r0.run_id}
    # 2. false claim; 3. reliable completion with a verification phase; 4. no submission
    specs = [("false_claim", sub[(sub.claim_success == 1) & (sub.resolved == 0)]),
             ("verified_success", sub[(sub.resolved == 1) & sub.phase_seq.str.split().apply(lambda p: "V" in p)]),
             ("not_submitted", runs[runs.submitted == 0])]
    for typ, cand in specs:
        cand = _prefer_len(cand[~cand.run_id.isin(used)])
        r = cand.iloc[rng.integers(len(cand))]
        out.append({"type": typ, "run_id": r.run_id, "task_id": r.task_id})
        used.add(r.run_id)
    (OUT / "examples.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def zip_demo_lib() -> Path:
    """demo_lib.zip with fixed timestamps (byte-identical on every build)."""
    p = OUT / "demo_lib.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for f in ("__init__.py", "phases.py", "features.py"):
            info = zipfile.ZipInfo(f"demo_lib/{f}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (ROOT / "demo_lib" / f).read_bytes())
    return p


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    stats = build()
    print("built", stats, flush=True)
    chk = check_demo_lib()
    print("demo_lib check:", {k: (v["max_abs_diff"], v["runs_with_phase_mismatch"]) for k, v in chk.items()
                              if isinstance(v, dict)}, flush=True)
    ex = pick_examples()
    print("examples:", ex, flush=True)
    zip_demo_lib()
    assets = ["runs.parquet", "steps.parquet", "examples.json", "demo_lib.zip"]
    sums = {a: sha256(OUT / a) for a in assets}
    sizes = {a: (OUT / a).stat().st_size for a in assets}
    (OUT / "SHA256SUMS").write_text("".join(f"{h}  {a}\n" for a, h in sums.items()), encoding="utf-8")
    (OUT / "checks.json").write_text(json.dumps({**stats, "demo_lib_check": chk, "examples": ex, "sha256": sums,
                                                 "sizes": sizes}, indent=1), encoding="utf-8")
    print(json.dumps({"sha256": sums, "sizes": sizes}, indent=1))


if __name__ == "__main__":
    main()
