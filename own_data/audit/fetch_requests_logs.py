"""Label audit of the leaderboard on network-dependent tasks (own_data/LABEL_AUDIT_REQUESTS.md, steps 1 and 4).
No LLM API calls.

(1) Lists logs/ of all 16 corpus runs in the public bucket swe-bench-submissions (prefix from each run's
    metadata.yaml, code of src/adapters/swebench_bashonly.py) and writes the sizes of every test_output.txt and
    report.json to own_data/audit/lb_logs_listing.csv.gz — this is the basis of the size estimate (step 4); nothing
    except the psf/requests tasks is downloaded.
(2) For the psf/requests tasks of the corpus (data/demo/runs.parquet, repo == psf/requests) downloads from every run
    logs/<task>/{report.json, test_output.txt, patch.diff, job-output.json}. Every file is checked for secrets BEFORE
    it is saved (redaction code of own_data/diag/fetch_corpus.py: JSON fields like api_key/token/secret/password,
    z.ai-format and sk- keys -> <REDACTED>; this machine's ZAI_API_KEY must not occur). Saved to
    data/raw/label_audit/lb_requests/<run>/<task>/ (27 MB, not committed, AGENTS.md 2.3); per-file redaction counts,
    sizes and the download timing go to own_data/audit/lb_requests_manifest.json.
Run from the repository root:  python own_data/audit/fetch_requests_logs.py
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "own_data" / "diag"))
from src.adapters.swebench_bashonly import _get, _session, list_keys, load_cfg, run_prefix  # noqa: E402
from fetch_corpus import clean  # noqa: E402

OUT = ROOT / "own_data" / "audit"
RAW = ROOT / "data" / "raw" / "label_audit" / "lb_requests"
FILES = ("report.json", "test_output.txt", "patch.diff", "job-output.json")
WORKERS = 8


def main() -> None:
    cfg = load_cfg()
    runs_df = pd.read_parquet(ROOT / "data" / "demo" / "runs.parquet")
    tasks = sorted(runs_df.loc[runs_df.repo == "psf/requests", "task_id"].unique())
    session = _session(WORKERS)

    # (1) listing of logs/ for all runs
    rows, prefixes = [], {}
    t0 = time.time()
    for run in cfg["runs"]:
        sub = run["sub"]
        meta = yaml.safe_load((ROOT / "data" / "raw" / "swebench_bashonly" / sub / "metadata.yaml")
                              .read_text(encoding="utf-8"))
        prefix = prefixes[sub] = run_prefix(meta, cfg, sub)
        for key, size in list_keys(cfg, f"{prefix}/logs/", session):
            parts = key[len(prefix) + 1:].split("/")  # logs/<task>/<file>
            if len(parts) == 3:
                rows.append({"run_id": sub, "task_id": parts[1], "file": parts[2], "bytes": size})
        print(f"{sub}: listed, {sum(r['run_id'] == sub for r in rows)} log files")
    listing = pd.DataFrame(rows)
    listing.to_csv(OUT / "lb_logs_listing.csv.gz", index=False)
    list_s = time.time() - t0

    # (2) download the psf/requests logs, secrets check before saving
    jobs = [(sub, t, f) for sub in prefixes for t in tasks for f in FILES]
    present = set(zip(listing.run_id, listing.task_id, listing.file))

    def fetch(job):
        sub, task, name = job
        if job not in present:
            return job, {"status": "absent in listing"}
        r = _get(session, f"{cfg['s3_base']}/{prefixes[sub]}/logs/{task}/{name}")
        if r is None:
            return job, {"status": "absent"}
        data, n = clean(name, r.content)
        dest = RAW / sub / task / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return job, {"status": "ok", "bytes_source": len(r.content), "redactions": n}

    t1 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        res = list(ex.map(fetch, jobs))
    dl_s = time.time() - t1
    files = {f"{s}/{t}/{n}": v for (s, t, n), v in res}
    ok = [v for v in files.values() if v["status"] == "ok"]
    manifest = {
        "s3_base": cfg["s3_base"], "prefixes": prefixes, "tasks": tasks, "files_wanted": len(jobs),
        "files_ok": len(ok), "bytes_downloaded": sum(v["bytes_source"] for v in ok),
        "redactions_total": sum(v["redactions"] for v in ok),
        "timing": {"listing_s": round(list_s, 1), "download_s": round(dl_s, 1), "workers": WORKERS},
        "files": files,
    }
    (OUT / "lb_requests_manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False),
                                                       encoding="utf-8")
    print(f"downloaded {len(ok)}/{len(jobs)} files, {manifest['bytes_downloaded'] / 1e6:.2f} MB in {dl_s:.1f} s, "
          f"redactions: {manifest['redactions_total']}")
    for f, v in files.items():
        if v["status"] != "ok":
            print("  not downloaded:", f, v["status"])


if __name__ == "__main__":
    main()
