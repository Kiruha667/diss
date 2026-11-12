import importlib.util, json, warnings
from pathlib import Path
import numpy as np, pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score
warnings.filterwarnings("ignore")
ROOT = Path(".").resolve()
spec = importlib.util.spec_from_file_location("lb", ROOT / "review/scripts/01_leaderboard.py"); lb = importlib.util.module_from_spec(spec); spec.loader.exec_module(lb)
p = lb.load(); y = p.y.to_numpy(); g = p.task_id.to_numpy(); fold = lb.folds(g)
pf = pd.read_csv(ROOT / "outputs/e13_patch_features.csv.gz"); pf = pf[pf.corpus == "leaderboard"].set_index("run_id")
empty = pf.loc[p.run_id, "p_empty"].to_numpy().astype(bool)
rows = []
for c in sorted(p.combo.unique()):
    for name, keep in (("all", np.ones(len(p), bool)), ("no_empty", ~empty)):
        m = (p.combo == c).to_numpy() & keep
        r = np.zeros(len(p))
        for k in range(5):
            tr, te = m & (fold != k), m & (fold == k)
            r[te] = lb.lr().fit(p.loc[tr, lb.S].to_numpy(float), y[tr]).predict_proba(p.loc[te, lb.S].to_numpy(float))[:, 1]
        rows.append({"combo": c, "pop": name, "resolved": 1 - y[m].mean(), "auroc": roc_auc_score(y[m], r[m]), "n_empty": int((empty & (p.combo == c).to_numpy()).sum())})
t = pd.DataFrame(rows)
out = {}
for name, d in t.groupby("pop"):
    s = spearmanr(d.resolved, d.auroc)
    out[name] = {"rho": round(float(s.statistic), 3), "p": round(float(s.pvalue), 4), "mean_auroc": round(float(d.auroc.mean()), 4)}
print(out)
print(t.pivot(index="combo", columns="pop", values="auroc").round(3).join(t[t["pop"] == "all"].set_index("combo")[["resolved", "n_empty"]].round(3)).sort_values("resolved").to_string())
# S and M+S on the population without empty patches, refit
ne = ~empty
pp = p[ne].reset_index(drop=True); yy = pp.y.to_numpy(); ff = lb.folds(pp.task_id.to_numpy())
rs = {"S": lb.oof_blocks(pp, yy, ff, ["S"]), "M+S": lb.oof_blocks(pp, yy, ff, ["M", "S"]), "M": lb.oof_blocks(pp, yy, ff, ["M"])}
ref = {k: {"auroc": round(roc_auc_score(yy, s), 4), "within": round(float(lb.within_pooled(yy, s, pp.task_id.to_numpy())[0]), 4), "p@10": round(lb.p_at(yy, s, 0.1), 4)} for k, s in rs.items()}
print("refit without empty patches:", ref, "base", round(float(yy.mean()), 4), "n", len(pp))
out["refit_without_empty"] = ref
json.dump({"spearman": out, "table": t.round(4).to_dict("records")}, open(ROOT / "review/results/10b_strength_no_empty.json", "w"), indent=1, default=float)
