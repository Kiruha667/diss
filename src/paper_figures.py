"""Figures of the paper kit (paper_kit/10_figures_tables.md) from outputs/ only; no model fitting, no API calls.

  python -m src.paper_figures  -> paper_kit/figures/fig1_triage.{pdf,png}, fig2_run_level.{pdf,png},
                                   fig3_variance.{pdf,png}, fig4_self_assessment.{pdf,png}

Colors: the reference categorical palette of the dataviz method (slots 1-4, light mode, fixed order); ceilings and
bases carry hatching and direct labels, so no figure depends on color alone (print, grayscale).
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.common import OUTPUTS, ROOT  # noqa: E402

OUT = ROOT / "paper_kit" / "figures"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
DEV, QWEN = "nano-agent-mistral_devstral-2512", "nano-agent-Qwen_Qwen3-32B"
plt.rcParams.update({"font.family": "sans-serif", "font.size": 8, "axes.edgecolor": BASE, "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK2, "axes.titlesize": 8.5, "axes.titleweight": "bold",
                     "pdf.fonttype": 42, "savefig.dpi": 300})


def _axes(ax, grid_axis="y"):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis=grid_axis, color=GRID, lw=0.6)
    ax.set_axisbelow(True)


def _save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(OUT / f"{name}.{ext}", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig1_triage() -> None:
    """Precision@10% on the leaderboard without the 132 empty patches (P_ne), with 95% CIs; base rate; ceiling D."""
    lb = pd.read_csv(OUTPUTS / "e15_leaderboard.csv")
    d = lb[(lb.population == "P_ne") & (lb.metric == "p@10")].set_index("model")
    summ = json.loads((OUTPUTS / "e15_summary.json").read_text(encoding="utf-8"))
    base = summ["leaderboard"]["P_ne"]["unresolved_share"]  # share unresolved in P_ne
    models = [("M", "model\n(M)"), ("M+N", "M +\nlog steps"), ("M+LEN", "M +\nlength"), ("S", "structure\n(S)"),
              ("M+S", "M + S"), ("D", "task\ndifficulty\n(ceiling)")]
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    for i, (m, lab) in enumerate(models):
        v, lo, hi = d.loc[m, ["value", "ci_lo", "ci_hi"]]
        ceiling = m == "D"
        ax.bar(i, v, width=0.62, color="white" if ceiling else BLUE, edgecolor=MUTED if ceiling else BLUE,
               hatch="////" if ceiling else None, lw=0.8)
        ax.errorbar(i, v, yerr=[[v - lo], [hi - v]], fmt="none", ecolor=INK, elinewidth=0.8, capsize=2)
        ax.text(i, hi + 0.02, f"{v:.3f}", ha="center", va="bottom", fontsize=7, color=INK)
    ax.axhline(base, color=ORANGE, lw=1.2, ls="--")
    ax.text(len(models) - 0.35, base, "base\nrate", color=INK2, fontsize=7, ha="left", va="center", clip_on=False)
    ax.set_xticks(range(len(models)), [lab for _, lab in models], fontsize=6.5)
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("precision@10% (share unresolved)")
    _axes(ax)
    _save(fig, "fig1_triage")


def fig2_run_level() -> None:
    """Within-unit AUROC on all pairs vs pairs of finished attempts (KTH, two settings), 95% CIs."""
    w = pd.read_csv(OUTPUTS / "e15_kth_within.csv")
    preds = [("not finished", "attempt not finished"), ("S-T2", "S (within setting)"), ("S-T1", "S (pooled)"),
             ("S+O-T2", "S+O (within setting)")]
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 2.2), sharey=True)
    for ax, (scope, title) in zip(axes, [(DEV, "devstral-2512"), ("all", "Qwen3-32B + devstral-2512")]):
        for j, (pairs, col, lab, off) in enumerate([("all", BLUE, "all pairs", -0.12), ("FF", ORANGE,
                                                     "both attempts finished", 0.12)]):
            for i, (p, _) in enumerate(preds):
                r = w[(w.scope == scope) & (w.predictor == p) & (w.pairs == pairs)].iloc[0]
                ax.errorbar(r.value, i + off, xerr=[[r.value - r.ci_lo], [r.ci_hi - r.value]], fmt="o" if j == 0
                            else "s", ms=4.5, color=col, mec="white", mew=0.6, elinewidth=1.0, capsize=0,
                            label=lab if i == 0 else None)
        ax.axvline(0.5, color=MUTED, lw=0.8, ls=":")
        ax.set_yticks(range(len(preds)), [lab for _, lab in preds])
        ax.invert_yaxis()
        ax.set_xlim(0.44, 0.66)
        ax.set_xlabel("within-unit AUROC")
        ax.set_title(title, loc="left")
        _axes(ax, "x")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=7, loc="lower center", ncol=2, bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    _save(fig, "fig2_run_level")


def fig3_variance() -> None:
    """Latent variance shares of the outcome on the KTH repeats (two settings, E8 population), with 95% CIs."""
    v = pd.read_csv(OUTPUTS / "e15_variance.csv")
    v = v[(v.population == "E8_labelled") & (v.scale == "latent")].set_index("component")
    comps = [("share_task", "task"), ("share_model", "model (setting)"), ("share_task_x_model", "task × model"),
             ("share_attempt", "attempt")]
    fig, ax = plt.subplots(figsize=(3.5, 1.6))
    for i, (key, lab) in enumerate(comps):
        x, lo, hi = (float(v.loc[key, c_]) * 100 for c_ in ("value", "ci_lo", "ci_hi"))
        ax.barh(i, x, height=0.55, color=BLUE)
        ax.errorbar(x, i, xerr=[[x - lo], [hi - x]], fmt="none", ecolor=INK, elinewidth=0.8, capsize=2)
        ax.text(hi + 1.2, i, f"{x:.1f}%", va="center", fontsize=7, color=INK)
    ax.set_yticks(range(len(comps)), [lab for _, lab in comps])
    ax.invert_yaxis()
    ax.set_xlim(0, 65)
    ax.set_xlabel("share of outcome variance, % (latent scale)")
    _axes(ax, "x")
    _save(fig, "fig3_variance")


def fig4_self_assessment() -> None:
    """Answers to the plain question C3 (E16 sample: finished devstral attempts), by outcome."""
    a = pd.read_csv(OUTPUTS / "e16_attempts.csv")
    a = a[a.C3_number.notna()]
    bins = [("< 90", lambda x: x < 90), ("95", lambda x: x == 95), ("100", lambda x: x == 100),
            ("other ≥ 90", lambda x: (x >= 90) & ~x.isin([95, 100]))]
    bins = [b for b in bins if bool(b[1](a.C3_number).any())]  # drop empty bins
    fig, ax = plt.subplots(figsize=(3.4, 2.0))
    width = 0.38
    for j, (lab, col, y) in enumerate([("solved", BLUE, 0), ("unresolved", ORANGE, 1)]):
        g = a[a.label == y].C3_number
        shares = [float(f(g).mean()) for _, f in bins]
        xs = np.arange(len(bins)) + (j - 0.5) * width
        ax.bar(xs, shares, width=width - 0.02, color=col, label=f"{lab} (n = {len(g)})")
    ax.set_xticks(range(len(bins)), [b for b, _ in bins])
    ax.set_xlabel("answer to “probability that the hidden tests pass” (0–100)")
    ax.set_ylabel("share of answers")
    ax.legend(frameon=False, fontsize=7)
    _axes(ax)
    _save(fig, "fig4_self_assessment")


def main() -> None:
    for f in (fig1_triage, fig2_run_level, fig3_variance, fig4_self_assessment):
        f()
        print("ok", f.__name__)


if __name__ == "__main__":
    main()
