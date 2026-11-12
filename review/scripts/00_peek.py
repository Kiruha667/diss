import pandas as pd, numpy as np
r = pd.read_parquet("data/demo/runs.parquet")
print(r.shape); print(list(r.columns))
s = r[r.submitted==1]
print("submitted", len(s), "unresolved share", 1-s.resolved.mean())
print(r.groupby("submitted").resolved.agg(["size","mean"]))
print(r[r.submitted==0].exit_status.value_counts().head(10))
g = s.groupby(["model","agent_version"]).agg(n=("resolved","size"), resolved=("resolved","mean"), steps=("n_steps","median"), claim=("claim_success","mean"), verif=("has_any_verification","mean"))
print(g.sort_values("resolved").round(3).to_string())
print(s.repo.value_counts().to_string())
k = pd.read_parquet("data/interim/agentic_evals/runs.parquet")
print(k.shape); print(list(k.columns))
print(k.groupby("setting").agg(n=("label","size"), unres=("label","mean"), fin=("ended_by_final_message","mean"), verif=("has_any_verification","mean"), steps=("n_steps","median")).round(3).to_string())
