"""Offline unit tests for the pieces where a silent bug would invalidate the thesis result."""
import json

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score
from statsmodels.stats.multitest import multipletests

from src.adapters.swebench_bashonly import clean_tool_output, parse_trajectory, read_label
from src.features import baseline_features
from src.folds import make_folds
from src.segment import compile_markers, segment, truncate_tracebacks
from src.stats import auroc, bh, evaluate, oof, prepare


# ---------------------------------------------------------------- labels / adapter


def test_read_label_flat_and_nested(tmp_path):
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"patch_is_None": False, "resolved": True}))
    nested = tmp_path / "nested.json"
    nested.write_text(json.dumps({"x__y-1": {"patch_is_None": False, "resolved": False}}))
    assert read_label(flat, "x__y-1") == 0          # resolved -> success -> label 0
    assert read_label(nested, "x__y-1") == 1        # unresolved -> failure -> label 1
    assert read_label(tmp_path / "missing.json", "x") is None


def test_clean_tool_output_keeps_nonzero_returncode():
    assert clean_tool_output("<returncode>0</returncode>\n<output>\nhello\n</output>") == "hello"
    assert clean_tool_output("<returncode>2</returncode>\n<output>boom</output>").startswith("returncode: 2")


def _v2_traj(exit_status="Submitted"):
    return {"info": {"exit_status": exit_status}, "trajectory_format": "mini-swe-agent-1.1", "messages": [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "<pr_description>\nConsider the following PR description:\nFix the bug in foo.\n</pr_description>\nrules"},
        {"role": "assistant", "content": "Looking.", "reasoning_content": "I should inspect foo first.",
         "reasoning": "I should inspect foo first.",  # duplicate source must not be concatenated
         "tool_calls": [{"function": {"name": "bash", "arguments": json.dumps({"command": "cat foo.py"})}}]},
        {"role": "tool", "content": "<returncode>0</returncode>\n<output>def foo(): pass</output>"},
        {"role": "assistant", "content": "Submit.", "tool_calls": [{"function": {"arguments": json.dumps(
            {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"})}}]},
        {"role": "exit", "content": "diff --git a/foo.py b/foo.py (the patch)"},
    ]}


def test_parse_v2_drops_exit_patch_and_dedups_reasoning():
    rec, reason = parse_trajectory(_v2_traj(), "20260217_mini-v2.0.0_x", "a__b-1", {"family": "f"}, {}, [])
    assert reason is None
    assert rec["task_text"] == "Fix the bug in foo."
    assert all("the patch" not in s["text"] for s in rec["steps"])
    a0 = rec["steps"][0]
    assert a0["text"].count("I should inspect foo first.") == 1
    assert a0["tool_calls"] == ["cat foo.py"] and "```bash\ncat foo.py\n```" in a0["text"]
    assert [s["role"] for s in rec["steps"]] == ["assistant", "tool", "assistant"]
    assert rec["agent_id"] == "mini-swe-agent-2.0.0"


def test_parse_v1_drops_trailing_submission_and_counts_single_bash_block():
    d = {"info": {"exit_status": "Submitted", "submission": "diff --git x"}, "messages": [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "<pr_description>Consider the following PR description:\nTask.</pr_description>"},
        {"role": "assistant", "content": "THOUGHT: two blocks.\n```bash\nls\n```\n```bash\npwd\n```"},
        {"role": "user", "content": "Please always provide EXACTLY ONE action in triple backticks."},
        {"role": "assistant", "content": "THOUGHT: ok.\n```python\nx=1\n```\n```bash\nls\n```"},
        {"role": "user", "content": "<returncode>0</returncode><output>a.py</output>"},
        {"role": "assistant", "content": "THOUGHT: done.\n```bash\necho MICRO_SWE_AGENT_FINAL_OUTPUT\n```"},
        {"role": "user", "content": "diff --git x"},
    ]}
    rec, reason = parse_trajectory(d, "20250726_mini-v1.0.0_m", "a__b-1", {}, {}, [])
    assert reason is None
    roles = [s["role"] for s in rec["steps"]]
    assert roles == ["assistant", "user", "assistant", "tool", "assistant"]   # trailing patch dropped
    assert rec["steps"][0]["tool_calls"] == []           # 2 bash blocks -> format error, nothing executed
    assert rec["steps"][2]["tool_calls"] == ["ls"]       # python block is not a command


def test_infra_error_is_excluded():
    rec, reason = parse_trajectory(_v2_traj("litellm.ServiceUnavailableError"), "s", "t", {}, {},
                                   ["ServiceUnavailableError"])
    assert rec is None and reason.startswith("infra_error")


# ---------------------------------------------------------------- segmentation


def test_traceback_truncation():
    tb = ["Traceback (most recent call last):"] + [f"  File \"x.py\", line {i}" for i in range(24)] + ["ValueError: bad"]
    out = truncate_tracebacks("before\n" + "\n".join(tb) + "\nafter").split("\n")
    assert out[0] == "before" and out[-1] == "after" and len(out) == 2 + 5


def test_markers_word_bounded_and_masked():
    mre = compile_markers(["resolved", "all tests pass"])
    rec = {"steps": [{"role": "assistant", "step_idx": 0, "tool_calls": [],
                      "text": "The issue is resolved and All Tests Pass now. Keep resolved_path intact please."}]}
    txt = " ".join(f.text for f in segment(rec, "reason_sent", mre))
    assert "[MASKED]" in txt and "resolved_path" in txt and "All Tests Pass" not in txt


def test_code_block_truncated_and_short_fragments_dropped():
    code = "\n".join(f"print('line number {i}')" for i in range(30))
    rec = {"steps": [{"role": "assistant", "step_idx": 0, "tool_calls": [],
                      "text": f"THOUGHT: Let me write the reproduction script.\n```python\n{code}\n```\nOk."}]}
    fr = [f.text for f in segment(rec, "reason_sent", None)]
    assert fr[0] == "Let me write the reproduction script."       # THOUGHT label removed
    assert len(fr) == 1 + 10                                     # 10 code lines, "Ok." (< 15 chars) dropped


# ---------------------------------------------------------------- baseline features on the prefix


def test_baseline_counts_only_completed_steps():
    lens = np.array([20, 30, 40, 50])
    steps = np.array([0, 0, 1, 2])            # the window ends inside step 2
    calls = [["h1"], ["h1"], ["h2"], ["h3"]]  # step 2's call may lie beyond the window -> not counted
    b = baseline_features(lens, steps, calls)
    assert b["n_steps"] == 3 and b["n_frag"] == 4 and b["total_chars"] == 140
    assert b["n_tool_calls"] == 2 and b["repeat_ratio"] == 1.0


# ---------------------------------------------------------------- statistics


def test_auroc_and_bh_match_reference():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 300)
    s = rng.normal(size=300) + y
    assert auroc(y, s) == pytest.approx(roc_auc_score(y, s))
    p = rng.uniform(size=50) ** 2
    assert np.allclose(bh(p), multipletests(p, method="fdr_bh")[1])


def _synthetic(signal: str, n_tasks=80, runs=12, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for t in range(n_tasks):
        diff = rng.normal()
        for r in range(runs):
            H, C = rng.normal(), rng.normal()
            n_steps = rng.normal()
            z = diff + (1.5 * H if signal == "chaos" else 1.5 * n_steps)
            rows.append({"run_id": f"t{t}/r{r}", "task_id": f"t{t}", "label": int(rng.uniform() < 1 / (1 + np.exp(-z))),
                         "H": H, "C": C, "fisher": 0.0, "n_steps": n_steps, "n_frag": 200, "total_chars": rng.normal(),
                         "mean_frag_len": rng.normal(), "n_tool_calls": rng.normal(), "repeat_ratio": rng.uniform()})
    df = pd.DataFrame(rows)
    fold = dict(zip(*make_folds(df.task_id).values.T))
    df["fold"] = df.task_id.map(fold)
    return df


def test_detects_real_chaos_signal():
    res, _, _ = evaluate(_synthetic("chaos"), n_boot=200)
    assert res["delta_ci_low"] > 0


def test_no_false_signal_when_label_depends_on_length_only():
    res, _, _ = evaluate(_synthetic("length"), n_boot=200)
    assert res["delta_ci_low"] <= 0 < res["auroc_base"] - 0.5


def test_fold_averaged_auroc_is_unbiased_without_signal():
    # strongly heterogeneous tasks (some always fail, some always succeed), features carry no signal:
    # fold-averaged AUROC must sit near 0.5, while pooled OOF AUROC is dragged below it.
    rng = np.random.default_rng(3)
    rows = []
    for t in range(40):
        p_fail = rng.choice([0.05, 0.5, 0.95])
        for r in range(10):
            rows.append({"run_id": f"t{t}/{r}", "task_id": f"t{t}", "label": int(rng.uniform() < p_fail),
                         "H": rng.normal(), "C": rng.normal(), "n_steps": rng.normal(), "n_frag": 200,
                         "total_chars": rng.normal(), "mean_frag_len": rng.normal(), "n_tool_calls": rng.normal(),
                         "repeat_ratio": rng.uniform()})
    df = pd.DataFrame(rows)
    df["fold"] = df.task_id.map(dict(zip(*make_folds(df.task_id).values.T)))
    res, _, _ = evaluate(df, n_boot=0)
    assert abs(res["auroc_full"] - 0.5) < 0.08
    assert res["auroc_pooled_full"] < res["auroc_full"]


def test_sign_test_detects_consistent_direction_only():
    from src.stats import within_task

    rng = np.random.default_rng(5)

    def make(shift):
        rows = []
        for t in range(120):
            for r in range(8):
                y = r % 2
                rows.append({"task_id": f"t{t}", "label": y, "H": rng.normal() - shift * y})
        return pd.DataFrame(rows)

    lower_in_failures = within_task(make(0.8), "H")
    assert lower_in_failures["share_positive"] < 0.35 and lower_in_failures["p_sign"] < 1e-4
    assert within_task(make(0.0), "H")["p_sign"] > 0.01


def test_oof_never_shares_tasks_between_train_and_test():
    df = _synthetic("chaos")
    clean, cols, _ = prepare(df)
    p = oof(clean, cols["full"])   # oof() asserts disjoint task sets per fold
    assert not np.isnan(p).any()
