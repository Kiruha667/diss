"""Single entry point (AGENTS.md section 6).

    python -m src.cli download   [--workers 16] [--tasks a,b] [--runs sub1,sub2]
    python -m src.cli inventory                                   # E0 (adapter build + report + folds)
    python -m src.cli segment    --seg reason_sent [--clauses]    # E1 segmentation + acceptance report
    python -m src.cli build      --seg reason_sent [--limit 200] [--encoder nomic]   # E1 embedding
    python -m src.cli features   --seg reason_sent --proj cos_step --N 200 --d 4     # E2 + E3
    python -m src.cli stats      --all                            # E4
    python -m src.cli smoke-mahmoud                               # E4 smoke test on precomputed step vectors

Stages must be run in order; each stage refuses to run if the previous one's outputs are missing.
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd

from src.common import (D_GRID, N_GRID, OUTPUTS, PROJS, RAW, REPORTS, SEED, SEGS, get_logger, interim_dir,
                        load_leak_markers, read_jsonl_gz, seed_everything, segments_path, trajectories_path,
                        write_jsonl_gz)

log = get_logger("cli")
SOURCE = "swebench_bashonly"


def _csv_list(s: str | None, cast=str):
    return [cast(x) for x in s.split(",")] if s else None


def cmd_download(a):
    from src.adapters import swebench_bashonly as ad

    cfg = ad.load_cfg()
    man = ad.download(cfg, RAW / SOURCE, workers=a.workers, tasks=_csv_list(a.tasks), runs=_csv_list(a.runs))
    log.info("downloaded: %d trajs, %d reports", int(man["traj_ok"].sum()), int(man["report_ok"].sum()))


def cmd_inventory(a):
    from src.adapters import swebench_bashonly as ad
    from src.inventory import run_inventory

    if not a.skip_build:
        ad.build(ad.load_cfg(), RAW / SOURCE, interim_dir(SOURCE))
    res = run_inventory(SOURCE)
    print(res)


def cmd_segment(a):
    from src.inventory import segmentation_report
    from src.segment import compile_markers, segment_record

    markers = load_leak_markers()
    if not markers:
        sys.exit("config/leak_markers.txt is missing: run `inventory` (E0) first")
    mre = compile_markers(markers)
    idx = pd.read_csv(OUTPUTS / "traj_index.csv", dtype={"run_id": str})
    label = dict(zip(idx.run_id, idx.label))
    stats = []
    for seg in (a.seg or SEGS):
        recs = (segment_record(r, seg, mre, clauses=a.clauses) for r in read_jsonl_gz(trajectories_path(SOURCE)))

        def tap(it, seg=seg):
            for r in it:
                stats.append({"run_id": r["run_id"], "seg": seg, "label": label[r["run_id"]],
                              "n_frag_total": r["n_frag_total"], "n_steps_total": r["n_steps_total"]})
                yield r

        n = write_jsonl_gz(segments_path(SOURCE, seg), tap(recs))
        log.info("segmented %s: %d trajectories", seg, n)
    st = pd.DataFrame(stats)
    prev = OUTPUTS / "segment_stats.csv"
    if prev.exists():  # keep stats of segs not re-run now
        old = pd.read_csv(prev, dtype={"run_id": str})
        st = pd.concat([old[~old.seg.isin(st.seg.unique())], st], ignore_index=True)
    st.to_csv(prev, index=False)
    ok = segmentation_report(SOURCE, st, clauses=a.clauses)
    print(f"E1 acceptance (median reason_sent >= 200): {ok}")


def _pilot_ids(n: int) -> set[str]:
    idx = pd.read_csv(OUTPUTS / "traj_index.csv", dtype={"run_id": str})
    ids = idx.sample(n=min(n, len(idx)), random_state=SEED)["run_id"]
    ids.to_frame().to_csv(OUTPUTS / "e1_pilot_runs.csv", index=False)
    return set(ids)


def _nomic_ids(n: int = 300, min_frag: int = 200) -> set[str]:
    """Control-encoder subsample: n trajectories, stratified by class, among those usable at N=200."""
    st = pd.read_csv(OUTPUTS / "segment_stats.csv", dtype={"run_id": str})
    pool = st[(st.seg == "reason_sent") & (st.n_frag_total >= min_frag)]
    frac = n / len(pool)
    sub = pd.concat([g.sample(n=max(1, round(len(g) * frac)), random_state=SEED)
                     for _, g in pool.groupby("label")])
    sub[["run_id", "label"]].to_csv(OUTPUTS / "nomic_subset.csv", index=False)
    return set(sub.run_id)


def cmd_build(a):
    from src.embed import embed_segments, embed_tasks, encoder_revision, load_encoder
    from src.segment import compile_markers, task_sentences

    seed_everything()
    segs = a.seg or list(SEGS)
    for seg in segs:
        if not segments_path(SOURCE, seg).exists():
            sys.exit(f"segments for {seg} missing: run `segment --seg {seg}` first")
    run_ids = None
    if a.limit:
        run_ids = _pilot_ids(a.limit)
    elif a.encoder == "nomic":
        run_ids = _nomic_ids()
    model = load_encoder(a.encoder, a.device)
    mre = compile_markers(load_leak_markers())
    tasks = {}
    for r in read_jsonl_gz(trajectories_path(SOURCE)):
        if (run_ids is None or r["run_id"] in run_ids) and r["task_id"] not in tasks:
            tasks[r["task_id"]] = task_sentences(r["task_text"], mre)
    t0 = time.time()
    embed_tasks(tasks, a.encoder, a.batch_size, model=model)
    total = 0
    for seg in segs:
        res = embed_segments(SOURCE, seg, a.encoder, run_ids=run_ids, batch_size=a.batch_size, model=model)
        total += res["embedded"]
    dt = time.time() - t0
    rev = encoder_revision(a.encoder)
    line = (f"| {time.strftime('%Y-%m-%d %H:%M')} | {a.encoder} | {rev} | {','.join(segs)} | "
            f"{'pilot ' + str(a.limit) if a.limit else ('subset' if run_ids else 'all')} | {total} | {dt:.0f} s |")
    log_path = REPORTS / "E1_embedding_log.md"
    if not log_path.exists():
        log_path.write_text("# E1 — журнал эмбеддинга\n\n| когда | энкодер | ревизия HF | seg | выборка | "
                            "траекторий | время |\n|---|---|---|---|---|---|---|\n", encoding="utf-8")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def cmd_features(a):
    from src.features import run_features

    seed_everything()
    for p in ("folds.csv", "traj_index.csv"):
        if not (OUTPUTS / p).exists():
            sys.exit(f"outputs/{p} missing: run `inventory` first")
    run_ids = None
    if a.encoder == "nomic":
        run_ids = set(pd.read_csv(OUTPUTS / "nomic_subset.csv", dtype={"run_id": str}).run_id)
    run_features(SOURCE, segs=a.seg, projs=a.proj or PROJS, Ns=a.N or N_GRID, ds=a.d or D_GRID,
                 encoder=a.encoder, workers=a.workers, run_ids=run_ids)


def cmd_stats(a):
    from src.stats import run_stats

    seed_everything()
    suffix = "" if a.encoder == "minilm" else f"_{a.encoder}"
    run_stats(OUTPUTS / f"features{suffix}.csv", OUTPUTS / "folds.csv", OUTPUTS / f"traj_meta{suffix}.csv",
              OUTPUTS, REPORTS / f"E4_results{suffix}.md", tag=f"main{suffix}",
              exclusions_csv=OUTPUTS / "exclusions.csv",
              window_exclusions_csv=OUTPUTS / f"window_exclusions{suffix}.csv", n_boot=a.n_boot)


def cmd_encoder_check(a):
    from src.stats import encoder_check

    encoder_check(OUTPUTS / "features.csv", OUTPUTS / "features_nomic.csv", OUTPUTS / "folds.csv",
                  REPORTS / "E4_encoder_check.md", append_to=REPORTS / "E4_results.md")


def cmd_smoke(a):
    from src.adapters import mahmoud_vectors as mv

    seed_everything()
    raw = RAW / "mahmoud_vectors"
    if not a.skip_download:
        mv.download(raw)
    mv.build_smoke(raw, OUTPUTS / "smoke_mahmoud", n_boot=a.n_boot)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m src.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("download")
    s.add_argument("--workers", type=int, default=16)
    s.add_argument("--tasks")
    s.add_argument("--runs")
    s.set_defaults(fn=cmd_download)

    s = sub.add_parser("inventory")
    s.add_argument("--skip-build", action="store_true", help="reuse data/interim trajectories")
    s.set_defaults(fn=cmd_inventory)

    s = sub.add_parser("segment")
    s.add_argument("--seg", action="append", choices=SEGS)
    s.add_argument("--clauses", action="store_true", help="E1 fallback: split sentences into clauses")
    s.set_defaults(fn=cmd_segment)

    s = sub.add_parser("build")
    s.add_argument("--seg", action="append", choices=SEGS)
    s.add_argument("--encoder", default="minilm", choices=["minilm", "nomic"])
    s.add_argument("--limit", type=int, help="pilot: embed only this many random trajectories (seed 42)")
    s.add_argument("--batch-size", type=int, default=256)
    s.add_argument("--device")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("features")
    s.add_argument("--seg", action="append", choices=SEGS)
    s.add_argument("--proj", action="append", choices=PROJS)
    s.add_argument("--N", action="append", type=int)
    s.add_argument("--d", action="append", type=int)
    s.add_argument("--encoder", default="minilm", choices=["minilm", "nomic"])
    s.add_argument("--workers", type=int)
    s.set_defaults(fn=cmd_features)

    s = sub.add_parser("stats")
    s.add_argument("--all", action="store_true", help="all tests 3-6 (the only mode)")
    s.add_argument("--encoder", default="minilm", choices=["minilm", "nomic"])
    s.add_argument("--n-boot", type=int, default=1000)
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("encoder-check", help="control encoder: MiniLM vs nomic on the same trajectories")
    s.set_defaults(fn=cmd_encoder_check)

    s = sub.add_parser("smoke-mahmoud")
    s.add_argument("--skip-download", action="store_true")
    s.add_argument("--n-boot", type=int, default=1000)
    s.set_defaults(fn=cmd_smoke)

    a = p.parse_args(argv)
    np.random.seed(SEED)
    a.fn(a)


if __name__ == "__main__":
    main()
