"""What litellm (agent venv) knows about glm-4.6 and z.ai — no API call. Run with /opt/own/venv-agent/bin/python."""
import litellm

for name in ("glm-4.6", "zai/glm-4.6", "openai/glm-4.6"):
    entry = litellm.model_cost.get(name)
    keys = ("litellm_provider", "input_cost_per_token", "output_cost_per_token", "cache_read_input_token_cost",
            "max_input_tokens", "max_output_tokens")
    print(name, "->", {k: entry.get(k) for k in keys} if entry else None)
    try:
        print("   provider:", litellm.get_llm_provider(name)[1])
    except Exception as e:  # noqa: BLE001
        print("   provider: ERROR", type(e).__name__, str(e)[:120])
print("zai in provider list:", "zai" in [str(p.value) if hasattr(p, "value") else str(p) for p in litellm.provider_list])
