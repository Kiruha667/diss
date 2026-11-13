"""The paper's population: the 7 732 submitted leaderboard runs without the 132 empty patches (P_ne, E15 §1.1).

Recomputes, with the stages' own code, the numbers that the paper kit quotes from analyses run on all 7 864 submitted
runs (review/PAPER_KIT_CHECK.md, Р1–Р5). No new protocol, no LLM calls, no downloads. Folds: the E7 folds of the full
population restricted to P_ne (as E15); models, metrics and the cluster bootstrap over tasks (1000, seed 42) are the
stages' functions.

  python -m src.paper_population run [g0 e7 e12 e13 e14]  -> outputs/paper_population.json (one section per part),
                                                             outputs/paper_population_metrics.csv, _diffs.csv,
                                                             outputs/paper_population_oof.csv.gz (E7 risks on P_ne)
  python -m src.paper_population report                    -> section «Популяция статьи» at the end of
                                                             reports/G0_false_success.md, E7, E12, E13, E14 reports

Parts:
  g0   G0 detector (has_claim of outputs/g0_final_messages.csv): claim share of resolved / unresolved runs, the
       difference with a CI (g0.claim_gap_bootstrap), the difference within combo weighted by the combo's runs (the
       reviewer's definition, review/scripts/05_e11_g0.py), FALSE_SUCCESS among unresolved, mutual information
       (src.g0_mutual_information). P_all is recomputed alongside as a check of the review's numbers.
  e7   E7 models (src.e7_sources.oof_risks) plus M+S+A and M+S+R (review С5: the protocol's base T+M+S is weaker than
       M+S). Check: S, M, M+S reproduce outputs/e15_leaderboard.csv (P_ne) to 1e-6.
  e12  E12 (src.e12_task_difficulty.oof_risks / evaluate); M is the E7 risk of P_ne (E12 itself reads the P_all one).
  e13  E13 transfer between generations (e13.transfer), block P (the e13.patch_models spec), per-combo AUROC of S
       and Spearman rho (as e13.combos, with the P_ne risk of S). Check: (M+S+P) − (M+S) reproduces the E13 post hoc
       row "non-empty diffs" (outputs/e13_summary.json).
  e14  E14 transfer to Opus 4.5 / 4.6 (e14.risks), corpus v2 and new runs without empty patches. Check: on the full
       populations the code reproduces outputs/e14_summary.json.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from src import e7_sources as e7
from src import e12_task_difficulty as e12
from src import e13_generations_patch as e13
from src import g0_false_success as g0
from src import g0_mutual_information as gmi
from src.common import OUTPUTS, REPORTS, SEED, get_logger

log = get_logger("paper_population")
JSON = OUTPUTS / "paper_population.json"
MET, DIF, OOF = (OUTPUTS / "paper_population_metrics.csv", OUTPUTS / "paper_population_diffs.csv",
                 OUTPUTS / "paper_population_oof.csv.gz")
N_EMPTY = 132
E7_SPEC = {**e7.SPEC, "M+S+A": (["M", "S"], ["A"]), "M+S+R": (["M", "S"], ["R"])}
E7_DIFFS = [("M+S+A", "M+S"), ("M+S+R", "M+S"), ("T+M+S+A", "T+M+S"), ("T+M+S+R", "T+M+S"), ("M+S", "M"),
            ("T+M+S", "T+M")]
E13_SPEC = {"P": ["P"], "S": ["S"], "S+P": ["S", "P"], "M+S": ["M", "S"], "M+S+P": ["M", "S", "P"]}
E14_DIFFS = [("S upper", "S transfer"), ("S+P transfer", "S transfer"), ("S+P upper", "S+P transfer")]


# ================================================================================ population and storage

def populations() -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """P_all (E7 population with texts) with the E7 folds and the empty-patch flag (E13 patch features)."""
    pop = e7.load_population(False)
    y = (1 - pop.resolved).to_numpy()
    fold = e7.folds_of(pop, y)
    pf = pd.read_csv(OUTPUTS / "e13_patch_features.csv.gz")
    pf = pf[pf.corpus == "leaderboard"].set_index("run_id")
    empty = pf.loc[pop.run_id, "p_empty"].to_numpy().astype(bool)
    if int(empty.sum()) != N_EMPTY:
        raise SystemExit(f"STOP: {int(empty.sum())} empty patches, expected {N_EMPTY}")
    return pop, fold, empty


def _p_ne(pop: pd.DataFrame, fold: np.ndarray, empty: np.ndarray):
    q = pop[~empty].reset_index(drop=True)
    return q, (1 - q.resolved).to_numpy(), fold[~empty]


def _save(part: str, summary: dict, met: list[dict] | None = None, dif: list[dict] | None = None) -> None:
    s = json.loads(JSON.read_text(encoding="utf-8")) if JSON.exists() else {}
    s[part] = summary
    JSON.write_text(json.dumps(s, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    for path, rows in ((MET, met), (DIF, dif)):
        if rows is None:
            continue
        new = pd.DataFrame(rows).assign(part=part)
        old = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=new.columns)
        out = pd.concat([old[old.part != part], new], ignore_index=True)
        out.round(6).to_csv(path, index=False)


def _rows(rows: list[dict], part: str) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "kind"} for r in rows]


# ================================================================================ G0

def within_combo(d: pd.DataFrame, n_boot: int = 1000, seed: int = SEED) -> tuple[float, float, float]:
    """Claim share of resolved minus unresolved within each combo, averaged with the combo's number of runs as the
    weight (combos with both outcomes; review/scripts/05_e11_g0.py: strat_diff); 95% CI by a bootstrap over tasks."""
    tasks, combos = np.sort(d.task_id.unique()), np.sort(d.combo.unique())
    ti, ci = pd.Index(tasks).get_indexer(d.task_id), pd.Index(combos).get_indexer(d.combo)
    r = d.resolved.to_numpy().astype(int)
    s_, n_ = np.zeros((len(tasks), len(combos), 2)), np.zeros((len(tasks), len(combos), 2))
    np.add.at(s_, (ti, ci, r), d.has_claim.to_numpy(float))
    np.add.at(n_, (ti, ci, r), 1.0)

    def stat(w: np.ndarray) -> float:
        s, n = np.einsum("t,tco->co", w, s_), np.einsum("t,tco->co", w, n_)
        ok = (n[:, 0] > 0) & (n[:, 1] > 0)
        diff = s[ok, 1] / n[ok, 1] - s[ok, 0] / n[ok, 0]
        size = n[ok].sum(1)
        return float((size * diff).sum() / size.sum())

    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(tasks), (n_boot, len(tasks)))
    boots = [stat(np.bincount(p, minlength=len(tasks)).astype(float)) for p in pick]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return stat(np.ones(len(tasks))), float(lo), float(hi)


def part_g0(pop: pd.DataFrame, fold: np.ndarray, empty: np.ndarray) -> dict:
    g = pd.read_csv(OUTPUTS / "g0_final_messages.csv", usecols=["run_id", "label", "has_claim", "cls"])
    d = pop[["run_id", "task_id", "combo", "resolved"]].assign(is_empty=empty).merge(g, on="run_id", how="left")
    assert d.has_claim.notna().all() and (d.label == 1 - d.resolved).all(), "G0 rows missing or labels differ"
    d["has_claim"] = d.has_claim.astype(bool)
    out = {"claim_share_empty_patches": float(d.has_claim[d.is_empty].mean()), "empty_patches": int(d.is_empty.sum())}
    for name, dd in (("P_ne", d[~d.is_empty].reset_index(drop=True)), ("P_all", d)):
        gap, (glo, ghi) = g0.claim_gap_bootstrap(dd)  # resolved minus unresolved (label 0 = resolved)
        w, wlo, whi = within_combo(dd)
        fail = dd[(dd.label == 1) & (dd.cls != "NO_FINAL_MESSAGE")]  # as G0: failed runs with a final message
        fs, (flo, fhi) = g0.task_bootstrap_share(fail, fail.cls == "FALSE_SUCCESS")
        per = dd.groupby("combo").apply(lambda x: x.has_claim[x.resolved == 1].mean() - x.has_claim[x.resolved == 0].mean())
        mi = gmi.mutual_information(dd.has_claim.astype(int).to_numpy(), dd.resolved.to_numpy())
        out[name] = {"runs": len(dd), "resolved": int(dd.resolved.sum()), "unresolved": int((1 - dd.resolved).sum()),
                     "claim_resolved": float(dd.has_claim[dd.resolved == 1].mean()),
                     "claim_unresolved": float(dd.has_claim[dd.resolved == 0].mean()),
                     "diff_pooled": [gap, glo, ghi], "diff_within_combo": [w, wlo, whi],
                     "combos_positive_diff": int((per > 0).sum()), "combos": int(per.notna().sum()),
                     "false_success_share_unresolved": [fs, flo, fhi],
                     "no_final_message": int((dd.cls == "NO_FINAL_MESSAGE").sum()),
                     "mutual_information_bits": mi["mutual_information_bits"],
                     "outcome_entropy_bits": mi["outcome_entropy_bits"], "normalized_mi": mi["normalized_mi"]}
        log.info("G0 %s: %s", name, out[name])
    _save("g0", out)
    return out


# ================================================================================ E7

def part_e7(pop: pd.DataFrame, fold: np.ndarray, empty: np.ndarray) -> dict:
    t0 = time.time()
    q, y, f = _p_ne(pop, fold, empty)
    risk, info = e7.oof_risks(q, y, f, E7_SPEC)
    # check: S, M, M+S reproduce E15 part 1 (P_ne, same folds and model)
    lb = pd.read_csv(OUTPUTS / "e15_leaderboard.csv")
    lb = lb[lb.population == "P_ne"].set_index(["model", "metric"]).value
    task_rows = list(pd.Series(np.arange(len(q))).groupby(q.task_id.to_numpy()).apply(np.array))
    check = {}
    for m in ("S", "M", "M+S"):
        c, n = e7.pair_counts(y, risk[m], task_rows)
        got = {"auroc": e7.auroc(y, risk[m]), "within": float(c.sum() / n.sum())}
        check[m] = {k: [round(v, 6), float(lb[(m, k)])] for k, v in got.items()}
        if any(abs(a - b) > 1e-6 for a, b in check[m].values()):
            raise SystemExit(f"STOP: {m} on P_ne {check[m]} differs from outputs/e15_leaderboard.csv")
    met, dif = e13.boot_eval(q.task_id.to_numpy(), y, risk, E7_DIFFS, "P_ne")
    pd.DataFrame({"run_id": q.run_id, "task_id": q.task_id, "y_unresolved": y, "fold": f,
                  **{m: risk[m] for m in risk}}).round(8).to_csv(OOF, index=False)
    out = {"runs": len(q), "tasks": len(task_rows), "unresolved_share": float(y.mean()),
           "check_vs_e15": check, "convergence_warnings": info["convergence_warnings"],
           "seconds": round(time.time() - t0, 1)}
    _save("e7", out, _rows(met, "e7"), _rows(dif, "e7"))
    return out


def _oof() -> pd.DataFrame:
    if not OOF.exists():
        raise SystemExit("run the e7 part first (outputs/paper_population_oof.csv.gz)")
    return pd.read_csv(OOF)


# ================================================================================ E12

def part_e12(pop: pd.DataFrame, fold: np.ndarray, empty: np.ndarray) -> dict:
    q, y, f = _p_ne(pop, fold, empty)
    oof = _oof()
    assert (oof.run_id.to_numpy() == q.run_id.to_numpy()).all()
    risk, info = e12.oof_risks(q, y, f)
    risk["M"] = oof["M"].to_numpy(float)  # E12 reads M from the P_all E7 run; here the P_ne one
    gap = float(np.abs(risk["M+S"] - oof["M+S"].to_numpy()).max())
    if gap > 1e-6:
        raise SystemExit(f"STOP: E12 M+S differs from the E7 M+S on P_ne by {gap}")
    met, dif = e12.evaluate(q, y, risk)
    out = {"runs": len(q), "M+S_vs_e7_p_ne_max_abs_diff": gap, "convergence_warnings": info["convergence_warnings"],
           "pca_explained_variance": info["pca_explained_variance"]}
    met, dif = met.assign(scope="P_ne"), dif.assign(scope="P_ne")
    _save("e12", out, met.to_dict("records"), dif.to_dict("records"))
    return out


# ================================================================================ E13

def part_e13(pop: pd.DataFrame, fold: np.ndarray, empty: np.ndarray) -> dict:
    q, y, f = _p_ne(pop, fold, empty)
    tr, info = e13.transfer(q, y, f)
    met = [r for r in tr.to_dict("records") if r["kind"] == "model"]
    dif = [r for r in tr.to_dict("records") if r["kind"] == "diff"]
    # block P (the e13.patch_models spec, without its P_all reproduction stop)
    lbp = pd.read_parquet(e13.OUT_DIR / "lb_patches.parquet").set_index("run_id")
    assert int(lbp.loc[q.run_id, "p_empty"].sum()) == 0
    p = e13.p_block(lbp.loc[q.run_id])
    risk = {m: np.zeros(len(q)) for m in E13_SPEC}
    for k in range(5):
        trk, te = f != k, f == k
        blk = e13.blocks_of(q, trk, p)
        for name, bl in E13_SPEC.items():
            risk[name][te], _ = e13.fit_predict(np.hstack([blk[b] for b in bl]), y, trk, te)
    m2, d2 = e13.boot_eval(q.task_id.to_numpy(), y, risk, [("M+S+P", "M+S"), ("S+P", "S")], "patch P_ne")
    ref = json.loads((OUTPUTS / "e13_summary.json").read_text(encoding="utf-8"))["posthoc_patch"]["non-empty diffs"]
    got = next(r for r in d2 if r["model"] == "M+S+P" and r["metric"] == "auroc")
    check = {"posthoc_non_empty": [ref["auroc"]["diff"], ref["auroc"]["ci_lo"], ref["auroc"]["ci_hi"]],
             "here": [got["diff"], got["ci_lo"], got["ci_hi"]]}
    if any(abs(a - b) > 1e-9 for a, b in zip(*check.values())):
        raise SystemExit(f"STOP: block P on P_ne {check}")
    met += m2
    dif += d2
    # per-combo AUROC of S (P_ne risk of the E7 S model) and Spearman rho with the resolved share (as e13.combos)
    oof = _oof()
    d = q[["combo"]].assign(y=y, S=oof["S"].to_numpy())
    cb = pd.DataFrame([{"combo": c, "runs": len(g), "resolved_share": float(1 - g.y.mean()),
                        "auroc_S": e7.auroc(g.y.to_numpy(), g.S.to_numpy())} for c, g in d.groupby("combo")])
    rho = float(spearmanr(cb.resolved_share, cb.auroc_S).statistic)
    rng = np.random.default_rng(SEED)
    boot = [spearmanr(cb.resolved_share.to_numpy()[ix], cb.auroc_S.to_numpy()[ix]).statistic
            for ix in (rng.integers(0, len(cb), len(cb)) for _ in range(e13.N_BOOT))]
    lo, hi = np.nanpercentile(boot, [2.5, 97.5])
    out = {"runs": len(q), "transfer": info, "check_patch": check,
           "combos": {"n": len(cb), "auroc_S_mean": float(cb.auroc_S.mean()), "auroc_S_min": float(cb.auroc_S.min()),
                      "auroc_S_max": float(cb.auroc_S.max()), "combo_min": cb.combo[cb.auroc_S.idxmin()],
                      "combo_max": cb.combo[cb.auroc_S.idxmax()], "spearman_rho": [rho, float(lo), float(hi)],
                      "table": cb.round(6).to_dict("records")}}
    _save("e13", out, _rows(met, "e13"), _rows(dif, "e13"))
    return out


# ================================================================================ E14

def part_e14(pop: pd.DataFrame, fold: np.ndarray, empty: np.ndarray) -> dict:
    from src import e14_new_models as e14

    fold_of = pd.Series(fold, index=pop.task_id).groupby(level=0).first()
    lbp = pd.read_parquet(e13.OUT_DIR / "lb_patches.parquet")
    corpus_all = pop[pop.agent_version == "2.0.0"].merge(lbp, on="run_id", how="left").reset_index(drop=True)
    new_all = pd.read_csv(OUTPUTS / "e14_runs.csv", dtype={"task_id": str})
    assert len(corpus_all) == 3956 and len(new_all) == 985

    def run(corpus, new, scope_prefix):
        rk = e14.risks(corpus, new, fold_of.loc[corpus.task_id].to_numpy(), fold_of.loc[new.task_id].to_numpy())
        yn = (1 - new.resolved).to_numpy()
        met, dif = [], []
        for scope, keep in [("new combos together", np.ones(len(new), bool))] + [
                (c, (new.combo == c).to_numpy()) for c in sorted(new.combo.unique())]:
            m, d = e13.boot_eval(new.task_id.to_numpy()[keep], yn[keep], {k: v[keep] for k, v in rk.items()},
                                 E14_DIFFS, f"{scope_prefix}{scope}")
            met += m
            dif += d
        return met, dif

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # within-task undefined for one combo (0 / 0), as in E14
        m_all, _ = run(corpus_all, new_all, "check P_all: ")
        ref = json.loads((OUTPUTS / "e14_summary.json").read_text(encoding="utf-8"))["criterion"]
        got = next(r for r in m_all if r["scope"] == "check P_all: new combos together" and r["model"] == "S transfer"
                   and r["metric"] == "auroc")
        check = {"e14_summary": [ref["auroc_S_transfer"], ref["ci_lo"], ref["ci_hi"]],
                 "here": [got["value"], got["ci_lo"], got["ci_hi"]]}
        if any(abs(a - b) > 1e-9 for a, b in zip(*check.values())):
            raise SystemExit(f"STOP: E14 reproduction {check}")
        corpus = corpus_all[corpus_all.p_empty == 0].reset_index(drop=True)
        new = new_all[new_all.p_empty == 0].reset_index(drop=True)
        met, dif = run(corpus, new, "")
    out = {"corpus_v2_runs": len(corpus), "corpus_v2_empty_dropped": int(len(corpus_all) - len(corpus)),
           "new_runs": len(new), "new_empty_dropped": int(len(new_all) - len(new)),
           "new_unresolved": int((1 - new.resolved).sum()), "check_vs_e14": check}
    _save("e14", out, met, dif)
    return out


PARTS = {"g0": part_g0, "e7": part_e7, "e12": part_e12, "e13": part_e13, "e14": part_e14}


def run(parts: list[str]) -> None:
    pop, fold, empty = populations()
    for p in parts:
        t0 = time.time()
        PARTS[p](pop, fold, empty)
        log.info("part %s done (%.0f s)", p, time.time() - t0)


# ================================================================================ report sections

HEAD = "## Популяция статьи (7 732 прогона без пустых патчей)"
INTRO = ("Добавлено 2026-11-13 по проверке набора для статьи (`review/PAPER_KIT_CHECK.md`, {why}). Популяция статьи — "
         "7 732 отправленных прогона лидерборда без 132 пустых патчей (E15, §1.1). Пересчёт тем же кодом этапа: "
         "`python -m src.paper_population run {part}` вызывает его функции; фолды — фолды полной популяции, "
         "ограниченные на 7 732 (как в E15); бутстрэп по задачам, 1000 повторов, seed 42; 95% ДИ. Протокол этапа и "
         "его критерии не менялись: это пересчёт на другой популяции, а не новая проверка. Выход — "
         "`outputs/paper_population.json` (раздел `{part}`), `outputs/paper_population_metrics.csv`, `_diffs.csv`.")


def _m(x: float) -> str:
    return f"{x:.3f}".replace("-", "−")


def _s(x: float, nd: int = 3) -> str:
    v = f"{x:+.{nd}f}"
    return ("−" + v[1:]) if v.startswith("-") else v


def ci(v: float, lo: float, hi: float) -> str:
    return f"{_m(v)} [{_m(lo)}; {_m(hi)}]"


def dci(v: float, lo: float, hi: float, nd: int = 3) -> str:
    return f"{_s(v, nd)} [{_s(lo, nd)}; {_s(hi, nd)}]"


def pp(v: float, lo: float, hi: float) -> str:
    return f"{_s(100 * v, 1)} п.п. [{_s(100 * lo, 1)}; {_s(100 * hi, 1)}]"


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _put(path, lines: list[str]) -> None:
    text = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    if HEAD in text:
        text = text[:text.index(HEAD)]
        while text and not text[-1].strip():
            text.pop()
    path.write_text("\n".join(text + ["", HEAD, ""] + lines) + "\n", encoding="utf-8", newline="\n")


def _get(df: pd.DataFrame, **kw) -> pd.Series:
    m = np.ones(len(df), bool)
    for k, v in kw.items():
        m &= (df[k] == v).to_numpy()
    assert m.sum() == 1, (kw, int(m.sum()))
    return df[m].iloc[0]


def report() -> None:
    """Section «Популяция статьи» in the report of every part present in outputs/paper_population.json."""
    s = json.loads(JSON.read_text(encoding="utf-8"))
    met, dif = pd.read_csv(MET), pd.read_csv(DIF)
    for part, fn in (("g0", _sec_g0), ("e7", _sec_e7), ("e12", _sec_e12), ("e13", _sec_e13), ("e14", _sec_e14)):
        if part in s:
            fn(s, met, dif)
            log.info("section written: %s", part)


def _sec_g0(s, met, dif):
    g, ne, al = s["g0"], s["g0"]["P_ne"], s["g0"]["P_all"]
    assert ne["diff_pooled"][1] < 0 < ne["diff_pooled"][2] and ne["diff_within_combo"][1] > 0
    gmi_all = json.loads((OUTPUTS / "g0_mutual_information.json").read_text(encoding="utf-8"))["submitted_7864"]
    L = [INTRO.format(why="Р1–Р2", part="g0"), "",
         "Детектор и паттерны G0 те же (`has_claim` из `outputs/g0_final_messages.csv`); исход — resolved. Прогоны без "
         "финального сообщения считаются прогонами без заявления. Разность внутри связки — разность долей в каждой "
         "из 16 связок, усреднённая с весом числа прогонов связки (определение рецензии, `review/scripts/05_e11_g0.py`). "
         "Столбец 7 864 пересчитан тем же кодом для сравнения.", "",
         "| | 7 732, без пустых патчей | 7 864, все отправленные |", "|---|---|---|",
         f"| прогонов (решённых / нерешённых) | {_num(ne['runs'])} ({_num(ne['resolved'])} / {_num(ne['unresolved'])}) | "
         f"{_num(al['runs'])} ({_num(al['resolved'])} / {_num(al['unresolved'])}) |",
         f"| заявление успеха у решённых | {pct(ne['claim_resolved'])} | {pct(al['claim_resolved'])} |",
         f"| заявление успеха у нерешённых | {pct(ne['claim_unresolved'])} | {pct(al['claim_unresolved'])} |",
         f"| разность (решённые − нерешённые), в целом | {pp(*ne['diff_pooled'])} | {pp(*al['diff_pooled'])} |",
         f"| разность внутри связки | {pp(*ne['diff_within_combo'])} | {pp(*al['diff_within_combo'])} |",
         f"| связок, где у решённых заявлений больше | {ne['combos_positive_diff']} из {ne['combos']} | "
         f"{al['combos_positive_diff']} из {al['combos']} |",
         f"| FALSE_SUCCESS среди нерешённых с финальным сообщением | {pct(ne['false_success_share_unresolved'][0])} "
         f"[{pct(ne['false_success_share_unresolved'][1])}; {pct(ne['false_success_share_unresolved'][2])}] | "
         f"{pct(al['false_success_share_unresolved'][0])} |",
         f"| взаимная информация «заявление — исход», бит | {ne['mutual_information_bits']:.5f} | "
         f"{al['mutual_information_bits']:.5f} |",
         f"| то же, доля энтропии исхода | {100 * ne['normalized_mi']:.3f}% | {100 * al['normalized_mi']:.3f}% |", "",
         f"- Столбец 7 864 совпадает с `outputs/g0_mutual_information.json` (взаимная информация "
         f"{gmi_all['mutual_information_bits']:.5f} бит) и с числами рецензии (`review/REVIEW.md`, С3; интервалы "
         "отличаются в третьем знаке — другие выборки бутстрэпа).",
         f"- Заявление успеха есть у {pct(g['claim_share_empty_patches'])} из {g['empty_patches']} пустых патчей (все "
         "не решены). На 7 864 они дают заметную часть общей отрицательной разности.",
         "- **Вывод на популяции статьи:** в целом доли заявлений у решённых и нерешённых не различаются (интервал "
         "включает ноль); внутри связки у решённых заявлений немного больше. Смена знака при переходе к сравнению "
         "внутри связки есть только на 7 864, где общая разность отрицательна из-за пустых патчей; на 7 732 общая "
         "разность от нуля не отличается, поэтому формулировка «парадокс Симпсона» к популяции статьи не относится.",
         "- Числа — о заявлениях, которые нашёл детектор; его полнота около 0.6 (`reports/G0_HUMAN_CHECK.md`). Проверка "
         "по 100 сообщениям считалась на 7 864 и не пересчитывалась."]
    _put(REPORTS / "G0_false_success.md", L)


def _sec_e7(s, met, dif):
    e = s["e7"]
    m7, d7 = met[met.part == "e7"], dif[dif.part == "e7"]
    a7 = pd.read_csv(OUTPUTS / "e7_diffs.csv")
    a7 = a7[a7.population == "all"]
    rows = []
    for a, b in E7_DIFFS:
        r = {k: _get(d7, model=a, baseline=b, metric=k) for k in ("auroc", "within")}
        old = a7[(a7.model == a) & (a7.baseline == b)]
        olds = {k: (dci(*old[old.metric == k][["diff", "ci_lo", "ci_hi"]].iloc[0]) if len(old) else "—")
                for k in ("auroc", "within")}
        rows.append(f"| `({a}) − ({b})` | {dci(r['auroc']['diff'], r['auroc'].ci_lo, r['auroc'].ci_hi)} | "
                    f"{dci(r['within']['diff'], r['within'].ci_lo, r['within'].ci_hi)} | {olds['auroc']} | {olds['within']} |")
    mrows = []
    for m in ("M", "S", "M+S", "M+S+A", "M+S+R", "T", "T+M", "T+M+S", "T+M+S+A", "T+M+S+R"):
        a, w = _get(m7, model=m, metric="auroc"), _get(m7, model=m, metric="within")
        mrows.append(f"| `{m}` | {ci(a.value, a.ci_lo, a.ci_hi)} | {ci(w.value, w.ci_lo, w.ci_hi)} |")
    ma, mr = (_get(d7, model=x, baseline="M+S", metric="auroc") for x in ("M+S+A", "M+S+R"))
    assert ma.ci_lo < 0 < ma.ci_hi and mr.ci_lo < 0 < mr.ci_hi
    wa = _get(d7, model="M+S+A", baseline="M+S", metric="within")
    assert wa.ci_hi < 0
    L = [INTRO.format(why="Р5, С5", part="e7"), "",
         "Модели и блоки — `src/e7_sources.py` (`oof_risks`, словарь `SPEC`). Добавлены `M+S+A` и `M+S+R`: база "
         "протокола `T+M+S` слабее своей подмодели `M+S` (рецензия, С5), поэтому прирост текстов агента проверяется и "
         f"относительно `M+S`. Проверка: `S`, `M`, `M+S` совпадают с E15 (P_ne) до 10⁻⁶. Прогонов {_num(e['runs'])}, "
         f"задач {e['tasks']}, доля нерешённых {e['unresolved_share']:.3f}.", "",
         "| модель | AUROC | AUROC внутри задачи |", "|---|---|---|", *mrows, "",
         "| сравнение | AUROC, 7 732 | внутри задачи, 7 732 | AUROC, 7 864 (этап) | внутри задачи, 7 864 (этап) |",
         "|---|---|---|---|---|", *rows, "",
         "- **Вывод на популяции статьи:** тексты агента (финальное сообщение A, рассуждения R) к `M+S` ничего не "
         "добавляют: оба интервала разности общего AUROC включают ноль; финальное сообщение внутри задачи различение "
         "даже ухудшает. Прирост относительно `T+M+S` — следствие слабой базы, а не вклад текста."]
    _put(REPORTS / "E7_REPORT.md", L)


def _sec_e12(s, met, dif):
    m12, d12 = met[met.part == "e12"], dif[dif.part == "e12"]
    a12 = pd.read_csv(OUTPUTS / "e12_diffs.csv")
    am12 = pd.read_csv(OUTPUTS / "e12_metrics.csv")
    rows = []
    for m in ("T2", "T2+M", "T2+M+S", "M+S", "M"):
        a = _get(m12, model=m, metric="auroc")
        o = _get(am12, model=m, metric="auroc")
        rows.append(f"| `{m}` | {ci(a.value, a.ci_lo, a.ci_hi)} | {ci(o.value, o.ci_lo, o.ci_hi)} |")
    for a, b, _ in e12.DIFFS:
        for k in ("auroc", "within"):
            r, o = _get(d12, model=a, baseline=b, metric=k), _get(a12, model=a, baseline=b, metric=k)
            rows.append(f"| `({a}) − ({b})`, {'AUROC' if k == 'auroc' else 'внутри задачи'} | "
                        f"{dci(r['diff'], r.ci_lo, r.ci_hi)} | {dci(o['diff'], o.ci_lo, o.ci_hi)} |")
    crit = _get(d12, model="T2+M+S", baseline="M+S", metric="auroc")
    assert crit.ci_lo < 0 < crit.ci_hi
    L = [INTRO.format(why="Р5", part="e12"), "",
         "Код — `src/e12_task_difficulty.py` (`oof_risks`, `evaluate`); `M` — риск E7 на 7 732 (сам E12 берёт `M` из "
         "прогона на 7 864). `M+S` из E12 совпадает с `M+S` E7 на 7 732.", "",
         "| модель или разность | 7 732 | 7 864 (этап) |", "|---|---|---|", *rows, "",
         "- **Вывод на популяции статьи** тот же: эмбеддинг описания задачи к `M+S` ничего не добавляет."]
    _put(REPORTS / "E12_REPORT.md", L)


def _sec_e13(s, met, dif):
    e = s["e13"]
    m13, d13 = met[met.part == "e13"], dif[dif.part == "e13"]
    t13 = pd.read_csv(OUTPUTS / "e13_transfer.csv")
    rows = []
    for scope, m in (("test v2", "S v1->v2"), ("test v2", "S v2->v2"), ("test v1", "S v2->v1"), ("test v1", "S v1->v1"),
                     ("test v2", "M+S v1->v2"), ("test v2", "M+S v2->v2"), ("test v1", "M+S v2->v1"),
                     ("test v1", "M+S v1->v1")):
        a = _get(m13, scope=scope, model=m, metric="auroc")
        o = _get(t13[t13.kind == "model"], scope=scope, model=m, metric="auroc")
        rows.append(f"| `{m.replace('->', ' → ')}` | {ci(a.value, a.ci_lo, a.ci_hi)} | {ci(o.value, o.ci_lo, o.ci_hi)} |")
    tr = e["transfer"]
    p13 = pd.read_csv(OUTPUTS / "e13_patch_diffs.csv")
    prow = []
    for a, b in (("M+S+P", "M+S"), ("S+P", "S")):
        for k in ("auroc", "within"):
            r, o = _get(d13, model=a, baseline=b, metric=k), _get(p13, model=a, baseline=b, metric=k)
            prow.append(f"| `({a}) − ({b})`, {'AUROC' if k == 'auroc' else 'внутри задачи'} | "
                        f"{dci(r['diff'], r.ci_lo, r.ci_hi, 4)} | {dci(o['diff'], o.ci_lo, o.ci_hi, 4)} |")
    pa = _get(m13, scope="patch P_ne", model="P", metric="auroc")
    cb, rho = e["combos"], e["combos"]["spearman_rho"]
    c13 = json.loads((OUTPUTS / "e13_summary.json").read_text(encoding="utf-8"))["combos"]
    L = [INTRO.format(why="Р3–Р5", part="e13"), "",
         f"Код — `src/e13_generations_patch.py` (`transfer`, спецификация `patch_models`, `combos`). Тест v2: "
         f"{_num(tr['test_v2']['runs'])} прогонов, тест v1: {_num(tr['test_v1']['runs'])} (на 7 864 в v1 было 121 из "
         "132 пустых патчей). Проверка: прирост блока P совпадает с пост-хок строкой «non-empty diffs» этапа.", "",
         "**Перенос между поколениями** (AUROC; `a → b` — обучение на поколении a, тест на b, задачи теста вне "
         "обучения):", "",
         "| модель | 7 732 | 7 864 (этап) |", "|---|---|---|", *rows, "",
         "**Блок P** (признаки финального патча):", "",
         "| разность | 7 732 | 7 864 (этап) |", "|---|---|---|", *prow,
         f"| `P` отдельно, AUROC | {ci(pa.value, pa.ci_lo, pa.ci_hi)} | — |", "",
         f"**Связки на 7 732:** {cb['n']}; доля решённых по связкам {min(r['resolved_share'] for r in cb['table']):.3f}–{max(r['resolved_share'] for r in cb['table']):.3f}.", "",
         f"**AUROC `S` по связке** (общая модель, риск E7 на 7 732): среднее {cb['auroc_S_mean']:.3f}, разброс "
         f"{cb['auroc_S_min']:.2f}–{cb['auroc_S_max']:.2f} ({cb['combo_min'].replace(' | ', ', ')} — {cb['combo_max'].replace(' | ', ', ')}); ρ Спирмена с "
         f"долей решённых по {cb['n']} связкам {_s(rho[0], 2)} [{_s(rho[1], 2)}; {_s(rho[2], 2)}] (на 7 864: "
         f"{_s(c13['spearman_rho'], 2)} [{_s(c13['ci_lo'], 2)}; {_s(c13['ci_hi'], 2)}]).", "",
         "- **Вывод на популяции статьи:** перенос между форматами по-прежнему теряет качество (числа в таблице); "
         "блок P к `M+S` добавляет то же, что на 7 864; связь силы модели с предсказуемостью при общей модели не "
         "установлена."]
    _put(REPORTS / "E13_REPORT.md", L)


def _sec_e14(s, met, dif):
    e = s["e14"]
    m14, d14 = met[met.part == "e14"], dif[dif.part == "e14"]
    a14 = pd.read_csv(OUTPUTS / "e14_metrics.csv")
    rows = []
    names = {"new combos together": "обе новые связки", "claude-4-5-opus@high | 2.0.0": "Opus 4.5 (high)",
             "claude-opus-4-6 | 2.0.0": "Opus 4.6"}
    for scope, name in names.items():
        for m in ("S transfer", "S upper", "S+P transfer"):
            a = _get(m14, scope=scope, model=m, metric="auroc")
            o = _get(a14[a14.kind == "model"], scope=scope, model=m, metric="auroc")
            rows.append(f"| {name} | `{m}` | {ci(a.value, a.ci_lo, a.ci_hi)} | {ci(o.value, o.ci_lo, o.ci_hi)} |")
        r = _get(d14, scope=scope, model="S+P transfer", baseline="S transfer", metric="auroc")
        o = _get(a14[a14.kind == "diff"], scope=scope, model="S+P transfer", baseline="S transfer", metric="auroc")
        rows.append(f"| {name} | `(S+P) − S`, перенос | {dci(r['diff'], r.ci_lo, r.ci_hi)} | "
                    f"{dci(o['diff'], o.ci_lo, o.ci_hi)} |")
    L = [INTRO.format(why="Р3", part="e14"), "",
         f"Код — `src/e14_new_models.py` (`risks`) и `e13.boot_eval`. Обучение — {_num(e['corpus_v2_runs'])} прогонов "
         f"v2.0.0 корпуса без {e['corpus_v2_empty_dropped']} пустых патчей; тест — прогоны Opus 4.5 и 4.6 без "
         f"{e['new_empty_dropped']} пустого патча: {_num(e['new_runs'])}, из них нерешённых {e['new_unresolved']}. Проверка: на полных "
         "популяциях код воспроизводит `outputs/e14_summary.json`.", "",
         "| тест | модель | AUROC, без пустых патчей | AUROC, с пустыми (этап) |", "|---|---|---|---|", *rows, "",
         "- **Вывод на популяции статьи** тот же: перенос `S` на новые модели того же формата не хуже обучения внутри "
         "новых связок; блок P при переносе прироста не даёт."]
    _put(REPORTS / "E14_REPORT.md", L)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run(sys.argv[2:] or list(PARTS))
    elif cmd == "report":
        report()
    else:
        raise SystemExit(__doc__)
