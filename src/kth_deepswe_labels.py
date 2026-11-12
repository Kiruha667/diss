"""Evidence that the harness labels of the KTH setting nano-agent x DeepSWE-Preview (t = 0.6) do not belong to its
nano-agent trajectories (reports/KTH_DEEPSWE_LABELS.md). No LLM calls, no downloads; reads data/raw/agentic_evals.

  python -m src.kth_deepswe_labels  -> outputs/kth_deepswe_labels.json

Per setting folder (the three settings of E8; the temperature-0 folders have result files only and give the file names):
1. names of the harness result files (one per run_k);
2. agreement of the patch in the trajectory (detailed_predictions.generated_diff, stripped) with the status of the same
   attempt in the result file: an attempt with an empty patch must be in empty_patch_ids, an attempt with a patch must
   not be;
3. labels of the attempts with an empty patch (resolved without a patch is impossible for an evaluation of that patch);
4. label disagreement on byte-identical patches within a unit (setting x task) — the same computation as E15 (3.3).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict

from src.adapters.agentic_evals import RAW_DIR, SETTINGS, _lit, iter_raw, labels_of, run_dirs
from src.common import OUTPUTS, get_logger

log = get_logger("kth_deepswe")
TEMP0 = ("nano-agent-Qwen_Qwen3-32B-temp0", "nano-agent-agentica-org_DeepSWE-Preview__temp0",
         "nano-agent-mistral_devstral-2512__temp0")


def _diff(raw: dict) -> str:
    dp = raw["detailed_predictions"]
    if not isinstance(dp, dict):
        dp = _lit(dp) or {}
    d = dp.get("generated_diff")
    return d.strip() if isinstance(d, str) and d != "None" else ""


def setting_evidence(setting: str) -> dict:
    files = sorted(p.name for r in run_dirs(setting) for p in r.glob("*.json"))
    cross = Counter()  # (patch in trajectory: empty / non-empty, status in result file)
    empty_labels = Counter()
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)  # (task, hash) -> labels
    per_run = []
    for rd in run_dirs(setting):
        lab = labels_of(rd)
        n_empty_traj = n_empty_file = 0
        for raw in iter_raw(rd):
            t = raw["instance_id"]
            d = _diff(raw)
            st = lab.get(t, "not_in_result_file")
            status = {0: "resolved", 1: "unresolved"}.get(st, st) if not isinstance(st, str) else st
            cross[("empty" if not d else "patch", status)] += 1
            n_empty_traj += not d
            if not d and isinstance(st, int):
                empty_labels["resolved" if st == 0 else "unresolved"] += 1
            if d and isinstance(st, int):
                groups[(t, hashlib.sha1(d.encode("utf-8")).hexdigest())].append(st)
        n_empty_file = sum(v == "empty_patch" for v in lab.values())
        per_run.append({"run": rd.name, "empty_patch_in_trajectories": n_empty_traj,
                        "empty_patch_ids_in_result_file": n_empty_file})
    pairs = disc = 0
    for ys in groups.values():
        m, f = len(ys), sum(ys)
        pairs += m * (m - 1) // 2
        disc += f * (m - f)
    return {"result_files": len(files), "result_files_named_nano_agent": sum("nano-agent" in f for f in files),
            "result_file_example": files[0],
            "trajectory_patch_x_result_status": {f"{a}|{b}": n for (a, b), n in sorted(cross.items())},
            "labelled_with_empty_patch": sum(empty_labels.values()),
            "labelled_with_empty_patch_resolved": empty_labels["resolved"],
            "labelled_with_empty_patch_unresolved": empty_labels["unresolved"],
            "identical_patch_pairs": pairs, "identical_patch_pairs_discordant": disc,
            "identical_patch_disagreement": disc / pairs if pairs else None, "per_run": per_run}


def main() -> dict:
    out = {"settings": {}, "temp0_result_files": {}}
    for s in SETTINGS:
        out["settings"][s] = setting_evidence(s)
        log.info("%s: %s", s, {k: v for k, v in out["settings"][s].items() if k != "per_run"})
    for s in TEMP0:
        names = sorted(p.name for p in (RAW_DIR / s).glob("run_*/*.json"))
        trajectories = sum(1 for _ in (RAW_DIR / s).glob("run_*/detailed_predictions.jsonl"))
        out["temp0_result_files"][s] = {"result_files": len(names),
                                        "named_nano_agent": sum("nano-agent" in n for n in names),
                                        "example": names[0] if names else None,
                                        "runs_with_trajectories": trajectories}
    path = OUTPUTS / "kth_deepswe_labels.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("-> %s", path)
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=1, ensure_ascii=False))
