"""Features of the M_struct+len model (E5) from step-level data, for the demo notebook.

The computation is src/e5_claims.process_record line by line; only the inputs differ. process_record reads the full
trajectory record; here every step carries what process_record derives from it:
  commands     - executed bash commands of the step (record `tool_calls`, full text)
  n_outputs    - number of tool outputs attached to the step
  n_nonzero_rc - how many of them have a non-zero return code ('returncode: N' prefix of the E0 adapter)
  n_sent       - number of E1 reason_sent fragments of the step's assistant text (src.segment.segment; precomputed,
                 because the full reasoning text is not shipped with the demo data)
Equality with process_record on the original records is checked by notebooks/build_demo_data.py.
"""
from __future__ import annotations

import math

import numpy as np

from demo_lib.phases import _norm_cmd, command_events

# Feature lists exactly as in src/e5_claims.py (LEN, STRUCT); M_struct+len = STRUCT + LEN without duplicates.
LEN = ["n_steps", "seg_len_steps", "seg_len_sent", "n_edits"]
STRUCT = ["has_any_verification", "n_verifications", "verif_after_last_edit", "n_verif_after_last_edit",
          "verified_before_first_edit", "n_steps_after_last_edit", "share_edit", "share_verify", "share_other",
          "n_transitions", "phase_bigram_entropy", "share_before_first_edit", "repeat_ratio", "distinct_cmd_ratio",
          "nonzero_rc_share", "n_files_edited", "n_file_switches"]
FEATURES = STRUCT + [c for c in LEN if c not in STRUCT]
BOOL_FEATURES = ("has_any_verification", "verif_after_last_edit", "verified_before_first_edit")  # cast to int (E5)
PHASE_CODES = {"edit": "E", "verify": "V", "other": "O"}


def step_events(steps: list[dict]) -> list[tuple[int, str, str]]:
    """(step_idx, kind, target) events in execution order (kind: edit | verify), as in process_record."""
    files: dict[str, str] = {}
    events = []
    for s in steps:
        for c in s["commands"]:
            for kind, target in command_events(c, files):
                events.append((s["step_idx"], kind, target))
    return events


def step_phases(steps: list[dict], events: list[tuple[int, str, str]] | None = None) -> list[str]:
    """Phase of every step: 'edit' if it has an edit event, else 'verify' if it has a verify event, else 'other'."""
    events = step_events(steps) if events is None else events
    phases = []
    for i in range(len(steps)):
        ks = {k for j, k, _ in events if j == i}
        phases.append("edit" if "edit" in ks else "verify" if "verify" in ks else "other")
    return phases


def trajectory_features(steps: list[dict]) -> dict:
    """M_struct+len features of one trajectory; `steps` = assistant steps in order, step_idx 0..n-1."""
    n = len(steps)
    events = step_events(steps)
    edit_steps = sorted({i for i, k, _ in events if k == "edit"})
    verif_steps = sorted({i for i, k, _ in events if k == "verify"})
    kinds = [k for _, k, _ in events]
    last_edit_ev = max((j for j, k in enumerate(kinds) if k == "edit"), default=None)
    first_edit_ev = min((j for j, k in enumerate(kinds) if k == "edit"), default=None)
    last_edit_step = edit_steps[-1] if edit_steps else -1

    seg_len_sent = sum(s["n_sent"] for s in steps if s["step_idx"] > last_edit_step)

    phases = step_phases(steps, events)
    bigrams = list(zip(phases, phases[1:]))
    if bigrams:
        _, cnt = np.unique(np.array([a + ">" + b for a, b in bigrams]), return_counts=True)
        p = cnt / cnt.sum()
        bigram_h = float(-(p * np.log2(p)).sum() / math.log2(9))
    else:
        bigram_h = 0.0
    cmds = [_norm_cmd(c) for s in steps for c in s["commands"]]
    reps = sum(1 for a, b in zip(cmds, cmds[1:]) if a == b)
    n_tools = sum(s["n_outputs"] for s in steps)
    nonzero = sum(s["n_nonzero_rc"] for s in steps)
    targets = [t for _, k, t in events if k == "edit" and t != "?"]
    switches = sum(1 for a, b in zip(targets, targets[1:]) if a != b)
    after = kinds[last_edit_ev + 1:] if last_edit_ev is not None else kinds

    seg_steps = n - (last_edit_step + 1)
    f = {
        "n_steps": n, "seg_len_steps": seg_steps, "seg_len_sent": seg_len_sent, "n_edits": len(edit_steps),
        "has_any_verification": bool(verif_steps), "n_verifications": len(verif_steps),
        "verif_after_last_edit": ("verify" in after) if last_edit_ev is not None else bool(verif_steps),
        "n_verif_after_last_edit": after.count("verify"),
        "verified_before_first_edit": "verify" in (kinds[:first_edit_ev] if first_edit_ev is not None else kinds),
        "n_steps_after_last_edit": seg_steps,
        "share_edit": phases.count("edit") / max(1, n), "share_verify": phases.count("verify") / max(1, n),
        "share_other": phases.count("other") / max(1, n),
        "n_transitions": sum(1 for a, b in bigrams if a != b), "phase_bigram_entropy": bigram_h,
        "share_before_first_edit": (edit_steps[0] / max(1, n)) if edit_steps else 1.0,
        "repeat_ratio": reps / (len(cmds) - 1) if len(cmds) > 1 else 0.0,
        "distinct_cmd_ratio": len(set(cmds)) / max(1, n),
        "nonzero_rc_share": nonzero / max(1, n_tools),
        "n_files_edited": len(set(targets)), "n_file_switches": switches,
    }
    for c in BOOL_FEATURES:
        f[c] = int(f[c])
    return f


def steps_from_frame(df) -> list[dict]:
    """Rows of the demo steps table (one run, any order) -> the step dicts used above."""
    d = df.sort_values("step_idx")
    return [{"step_idx": int(r.step_idx), "commands": [str(c) for c in r.commands], "n_outputs": int(r.n_outputs),
             "n_nonzero_rc": int(r.n_nonzero_rc), "n_sent": int(r.n_sent)} for r in d.itertuples(index=False)]
