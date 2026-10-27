"""Probe config (NOT part of the corpus): the PROTOCOL 1.0 run config + z.ai's `thinking: disabled`, sent through
litellm as extra_body. Purpose: check whether it restores the corpus behaviour of glm-4.6 (no reasoning_content).
Run from the repository root:  python own_data/infra/make_probe_config.py
"""
import hashlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
src = ROOT / "own_data/config/glm-4.6_v1.0.yaml"
out = ROOT / "own_data/config/probe_glm-4.6_thinking-disabled.yaml"
cfg = yaml.safe_load(src.read_text(encoding="utf-8"))
cfg["model"]["model_kwargs"]["extra_body"] = {"thinking": {"type": "disabled"}}
out.write_text("# PROBE ONLY, not a corpus config: glm-4.6_v1.0.yaml + model_kwargs.extra_body.thinking.type = disabled\n"
               + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=10_000), encoding="utf-8", newline="\n")
print(out.name, hashlib.sha256(out.read_bytes()).hexdigest())
