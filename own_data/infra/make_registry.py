"""Write own_data/config/litellm_model_registry.json (PROTOCOL.md 1.0, section 8, item 2).

The corpus config points to a registry file on the original runner's machine whose content is not published.
Reconstruction: the entry of litellm's own model map for "zai/glm-4.6" in the agent environment (litellm 1.80.7),
registered under the bare name "glm-4.6" with litellm_provider "openai" (z.ai's OpenAI-compatible endpoint, the
api_base of the config). Run with /opt/own/venv-agent/bin/python.
"""
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path

import litellm

OUT = Path("/mnt/c/Users/1/Desktop/diss/own_data/config/litellm_model_registry.json")
entry = copy.deepcopy(litellm.model_cost["zai/glm-4.6"])
entry["litellm_provider"] = "openai"
entry.setdefault("mode", "chat")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({"glm-4.6": entry}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print("litellm", importlib.metadata.version("litellm"), "| source entry: zai/glm-4.6")
print(json.dumps(entry, sort_keys=True))
print("sha256", hashlib.sha256(OUT.read_bytes()).hexdigest())
