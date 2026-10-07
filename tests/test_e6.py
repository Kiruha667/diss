"""Unit tests for E6 block 1 (src/e6_refine.py)."""
import math

import numpy as np
import pandas as pd

from src.e6_refine import (block1_record, command_files, fires, loop_features, norm_command, select, topk_counts,
                           vif)
from tests.test_e5 import _rec


def test_norm_command():
    assert norm_command("cd /testbed && sed -n '120,180p' django/db/models/query.py") == \
        norm_command("cd /testbed; sed -n '200,260p'   django/db/models/query.py")
    assert norm_command("python /tmp/repro_1.py") == norm_command("python /tmp/check.py")
    assert norm_command("git show 3f2a9c1b") == "git show H"
    assert norm_command("cat > a.py << 'EOF'\nx = 1\nEOF") != norm_command("cat > a.py << 'EOF'\ny = 2\nEOF")


def test_command_files():
    assert command_files("cd /testbed && sed -n '1,5p' /testbed/src/x.py | head") == {"src/x.py"}
    assert command_files("cat > ./fix.py << 'EOF'\nimport other.py\nEOF") == {"fix.py"}


def test_loop_features():
    steps = [["grep -rn foo src"], ["cat src/a.py"], ["sed -n '1,9p' src/a.py"], ["sed -n '10,19p' src/a.py"],
             [], ["cat src/a.py"]]
    f = loop_features(steps)
    assert f["repeat_ratio_norm"] == 1 / 4 and f["max_run_length"] == 2
    assert f["repeat_in_window_3"] == 2 / 5 and f["repeat_in_window_10"] == 2 / 5
    assert f["distinct_ratio"] == 3 / 6 and f["revisit_ratio"] == 3 / 6


def test_fires():
    df = pd.DataFrame({"distinct_ratio": [1.0, 0.5], "max_run_length": [1, 3], "repeat_ratio_norm": [0.0, 0.2]})
    assert [list(fires(df, c)) for c in df] == [[False, True]] * 3


def test_select_anchor_and_composition():
    rng = np.random.default_rng(0)
    n = 600
    y = rng.integers(0, 2, n)
    a = y + rng.normal(0, 1, n)
    sh = rng.dirichlet([1, 1, 1], n)
    df = pd.DataFrame({"label": y, "a": a, "phase_bigram_entropy": a + rng.normal(0, 0.2, n),
                       "c": rng.normal(size=n), "share_edit": sh[:, 0], "share_verify": sh[:, 1],
                       "share_other": sh[:, 2]})
    cols = ["a", "phase_bigram_entropy", "c", "share_edit", "share_verify", "share_other"]
    kept, dropped = select(df, cols)
    assert "phase_bigram_entropy" in kept and dropped["a"] == "phase_bigram_entropy"
    assert sum(v == "composition" for v in dropped.values()) == 1 and len(kept) == 4
    kept_pure, dropped_pure = select(df, cols, None)
    assert "a" in kept_pure and dropped_pure["phase_bigram_entropy"] == "a"
    v = vif(df, cols)
    assert math.isinf(v["share_edit"]) and v["c"] < 1.1


def test_topk_counts():
    y = np.array([1, 0, 1, 0, 0, 1, 0, 0, 0, 0])
    s = np.array([.9, .8, .1, .2, .3, .7, .6, .5, .4, .0])
    fold = np.array([0] * 5 + [1] * 5)
    assert topk_counts(y, s, fold, 0.2) == (2, 2)    # top-1 per fold: rows 0 and 5
    assert topk_counts(y, s, fold, 0.4) == (2, 4)    # rows 0, 1 and 5, 6


def test_block1_record():
    r = block1_record(_rec())   # phases: other edit verify edit verify other
    assert r["_phases"] == ["other", "edit", "verify", "edit", "verify", "other"]
    assert r["repeat_ratio_norm"] == 0 and r["repeat_in_window_3"] == 1 / 6
    assert r["verify_fail_share"] == 1 / 2 and r["max_phase_run"] == 1
    assert r["reverify_after_last_edit"] == 1
    assert math.isclose(r["phase_trigram_entropy"], 2 / math.log2(27))
    assert math.isclose(sum(r[k] for k in r if k.startswith("bg_")), 1)
    assert r["bg_edit_verify"] == 2 / 5
