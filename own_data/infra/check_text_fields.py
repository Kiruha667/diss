"""Which fields feed the text features (seg_len_sent, final message) for glm-4.6: the existing adapter
(src/adapters/swebench_bashonly.parse_trajectory) records the reasoning source per trajectory in
meta.reasoning_sources; the step text = reasoning (if any) + content + fenced commands, and demo_lib's n_sent comes
from the same text. Run from the repository root:  python own_data/infra/check_text_fields.py
"""
import glob
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.adapters.swebench_bashonly import load_cfg, parse_trajectory  # noqa: E402

SETS = {"corpus glm-4.6 (19)": "data/raw/swebench_bashonly/20251201_mini-v1.17.1_glm-4.6/trajs/*.traj.json",
        "smoke 1.0": "own_data/raw/own-v1.0_mini-v1.17.1_glm-4.6_smoke-20261027b/*/*.traj.json",
        "probe thinking disabled": "own_data/probes/probe-thinking-disabled-20261027/*/*.traj.json"}
infra = list(load_cfg().get("infra_exit_statuses") or [])
out = {}
for name, pat in SETS.items():
    src = Counter()
    for p in sorted(glob.glob(str(ROOT / pat))):
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        rec, reason = parse_trajectory(d, "x_mini-v1.17.1_glm-4.6", Path(p).name.split(".traj")[0], {}, {}, infra)
        src[tuple(rec["meta"]["reasoning_sources"]) if rec else ("not parsed: " + str(reason),)] += 1
    out[name] = {" + ".join(k) or "(none)": v for k, v in src.items()}
print(json.dumps(out, ensure_ascii=False))
