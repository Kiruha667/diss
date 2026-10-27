"""Download the corpus artifacts of run 20251201_mini-v1.17.1_glm-4.6 for the 5 pilot tasks from the same public
source as the corpus (S3 bucket swe-bench-submissions, keys from the run's metadata.yaml — the code of
src/adapters/swebench_bashonly.py): the trajectory, its submission (final patch) and everything under logs/<task>/
(the leaderboard evaluation artifacts, if present). No LLM API calls.

Every file is checked for secrets BEFORE it is saved: JSON fields named like api_key / token / secret / password
and any string in the z.ai key format (32 hex + '.' + 16 alphanumerics) or an 'sk-' key are replaced by <REDACTED>;
the number of replacements per file goes to manifest.json. The ZAI_API_KEY of this machine (if set) is checked too.
Run from the repository root:  python own_data/diag/fetch_corpus.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.adapters.swebench_bashonly import _get, _session, list_keys, load_cfg, run_prefix  # noqa: E402

RUN = "20251201_mini-v1.17.1_glm-4.6"
OUT = ROOT / "own_data" / "diag" / "corpus_glm46"
TASKS = [t["task_id"] for t in json.loads((ROOT / "own_data" / "tasks_pilot.json").read_text(encoding="utf-8"))["tasks"]]
KEY_RE = re.compile(r"\b[0-9a-f]{32}\.[A-Za-z0-9]{16}\b|\bsk-[A-Za-z0-9_-]{20,}")
SECRET_FIELD_RE = re.compile(r"api_key|token|secret|password", re.I)


def redact_json(o, counter):
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if SECRET_FIELD_RE.search(str(k)) and isinstance(v, str) and v and v != "<REDACTED>":
                out[k] = "<REDACTED>"
                counter[0] += 1
            else:
                out[k] = redact_json(v, counter)
        return out
    if isinstance(o, list):
        return [redact_json(v, counter) for v in o]
    if isinstance(o, str):
        new, n = KEY_RE.subn("<REDACTED>", o)
        counter[0] += n
        return new
    return o


def clean(name: str, data: bytes) -> tuple[bytes, int]:
    own_key = os.environ.get("ZAI_API_KEY") or ""
    text = data.decode("utf-8", errors="replace")
    counter = [0]
    if name.endswith(".json"):
        try:
            text = json.dumps(redact_json(json.loads(text), counter), indent=1, ensure_ascii=False)
        except json.JSONDecodeError:
            text, n = KEY_RE.subn("<REDACTED>", text)
            counter[0] += n
    else:
        text, n = KEY_RE.subn("<REDACTED>", text)
        counter[0] += n
    if own_key and own_key in text:
        raise RuntimeError(f"this machine's ZAI_API_KEY found in {name} - not saved")
    if KEY_RE.search(text):
        raise RuntimeError(f"key-like string still present in {name} - not saved")
    return text.encode("utf-8"), counter[0]


def main() -> None:
    cfg = load_cfg()
    meta = yaml.safe_load((ROOT / "data" / "raw" / "swebench_bashonly" / RUN / "metadata.yaml").read_text(encoding="utf-8"))
    prefix = run_prefix(meta, cfg, RUN)
    session = _session(4)
    manifest = {"run": RUN, "prefix": prefix, "s3_base": cfg["s3_base"], "tasks": {}}
    for task in TASKS:
        entry = {"files": {}}
        keys = list_keys(cfg, f"{prefix}/trajs/{task}/", session) + list_keys(cfg, f"{prefix}/logs/{task}/", session)
        entry["listed"] = [k for k, _ in keys]
        for key, size in keys:
            rel = key[len(prefix) + 1:]  # trajs/<task>/... or logs/<task>/...
            r = _get(session, f"{cfg['s3_base']}/{key}")
            if r is None:
                entry["files"][rel] = {"status": "absent"}
                continue
            data, n = clean(rel, r.content)
            dest = OUT / task / rel.split("/", 2)[0] / rel.split("/", 2)[2]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            entry["files"][rel] = {"bytes_source": size, "redactions": n}
        traj = OUT / task / "trajs" / f"{task}.traj.json"
        if traj.exists():
            info = json.loads(traj.read_text(encoding="utf-8")).get("info", {})
            sub = info.get("submission") or ""
            (OUT / task / "submission.diff").write_text(sub, encoding="utf-8", newline="\n")
            entry.update({"exit_status": info.get("exit_status"), "submission_chars": len(sub),
                          "model_stats": info.get("model_stats"), "mini_version": info.get("mini_version")})
        entry["has_eval_logs"] = any(k.startswith("logs/") for k in entry["files"])
        manifest["tasks"][task] = entry
        print(task, "files:", len(entry["files"]), "| eval logs:", entry["has_eval_logs"],
              "| redactions:", sum(f.get("redactions", 0) for f in entry["files"].values()))
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
