"""Unit tests for E5 (src/e5_claims.py)."""
import numpy as np

from src.e5_claims import command_events, gate_decision, process_record


def test_gate_thresholds():
    assert gate_decision(250) == {"tested": True, "N": 200, "d": 4, "biased": False}
    assert gate_decision(150)["N"] == 100 and gate_decision(60) == {"tested": True, "N": 50, "d": 3, "biased": True}
    assert gate_decision(23)["tested"] is False


def test_edit_targets():
    files = {}
    assert command_events("sed -i 's/a/b/' /testbed/django/db/query.py", files) == [("edit", "django/db/query.py")]
    assert command_events("cat > ./fix.py << 'EOF'\nx=1\nEOF", files) == [("edit", "fix.py")]
    assert command_events("python - <<'EOF'\nopen('/testbed/a.py','w').write('x')\nEOF", files) == [("edit", "a.py")]
    assert command_events("cd /testbed && python -m pytest -q", files) == [("verify", "")]


def _rec():
    def a(i, cmd, text="Some reasoning sentence that is long enough here."):
        return {"role": "assistant", "text": text + f"\n\n```bash\n{cmd}\n```", "step_idx": i, "tool_calls": [cmd]}

    def t(i, txt):
        return {"role": "tool", "text": txt, "step_idx": i, "tool_calls": []}

    steps = [a(0, "grep -rn foo src"), t(0, "src/x.py:1:foo"),
             a(1, "sed -i 's/a/b/' src/x.py"), t(1, ""),
             a(2, "python -m pytest -q"), t(2, "returncode: 1\nFAILED"),
             a(3, "sed -i 's/b/c/' src/y.py"), t(3, ""),
             a(4, "python -m pytest -q"), t(4, "1 passed"),
             a(5, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt", "The fix is complete and verified now.")]
    return {"run_id": "r/t", "task_id": "t", "agent_id": "mini-swe-agent-2.0.0", "model_id": "m",
            "model_family": "f", "label": 1, "meta": {"exit_status": "Submitted"}, "task_text": "Fix foo.",
            "steps": steps}


def test_process_record_structure():
    r = process_record(_rec(), None)
    assert r["n_steps"] == 6 and r["n_edits"] == 2 and r["seg_len_steps"] == 2   # steps 4, 5 after last edit (3)
    assert r["n_verifications"] == 2 and r["verif_after_last_edit"] and r["n_verif_after_last_edit"] == 1
    assert not r["verified_before_first_edit"] and r["n_files_edited"] == 2 and r["n_file_switches"] == 1
    assert r["nonzero_rc_share"] == 1 / 5 and r["share_before_first_edit"] == 1 / 6
    assert r["n_transitions"] == 5 and 0 < r["phase_bigram_entropy"] <= 1
    assert r["seg_len_sent"] > 0 and np.isclose(r["share_edit"] + r["share_verify"] + r["share_other"], 1)
