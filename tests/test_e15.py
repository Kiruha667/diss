"""Unit tests of the E15 helpers (no project data needed)."""
import numpy as np
import pytest
from scipy.special import expit

from src import e15_consolidation as e15


def test_prec_at_counts_ties_by_expectation():
    y = np.array([1, 0, 1, 0, 0, 0, 0, 0, 0, 0])
    s = np.array([3, 2, 2, 2, 2, 1, 1, 1, 1, 1], float)
    # top 2: the 3 for sure, then one of four tied 2s (one of which is a failure) -> 1 + 1/4
    assert e15.prec_at(y, s, 0.2) == pytest.approx((1 + 0.25) / 2)
    assert e15.recall_at(y, s, 0.2) == pytest.approx(1.25 / 2)
    # continuous scores: plain top-k
    s2 = np.arange(10, 0, -1).astype(float)
    assert e15.prec_at(y, s2, 0.3) == pytest.approx(2 / 3)


def test_unit_pair_counts_pair_sets():
    y = np.array([1, 1, 0, 0])
    fin = np.array([1, 0, 1, 0])
    r = np.array([0.9, 0.1, 0.5, 0.5])
    out = e15.unit_pair_counts(y, r, fin, [np.arange(4)])
    c, n = out["all"]
    assert n[0] == 4 and c[0] == 2  # (0.9 > 0.5) twice, (0.1 < 0.5) twice
    assert out["FF"][1][0] == 1 and out["FF"][0][0] == 1  # failure 0 (fin) vs success 2 (fin)
    assert out["UU"][1][0] == 1 and out["UU"][0][0] == 0  # failure 1 vs success 3, both cut off
    assert out["FU"][1][0] == 2


def test_glmm_recovers_parameters():
    rng = np.random.default_rng(1)
    T, n_att = 400, 10
    mu = np.array([1.0, 0.0, -1.0])
    a = rng.normal(0, 1.5, T)
    c = rng.normal(0, 0.8, (T, 3))
    n = np.full((T, 3), n_att, float)
    s = rng.binomial(n_att, expit(mu[None] + a[:, None] + c)).astype(float)
    th = e15.glmm_fit(s, n, 20)
    assert np.allclose(th[:3], mu, atol=0.3)
    assert np.exp(th[3]) == pytest.approx(1.5, abs=0.3)
    assert np.exp(th[4]) == pytest.approx(0.8, abs=0.3)


def test_ceiling_without_noise_is_one():
    th = np.array([0.5, 0.0, -0.5, np.log(1.5), np.log(1.0)])
    r = e15.simulate_ceiling(th, [0, 1, 2], 0.0, n_sim=3, n_tasks=100)
    assert r["within_ceiling"] == pytest.approx(1.0)
    r2 = e15.simulate_ceiling(th, [0], 0.10, n_sim=3, n_tasks=100)
    assert r2["within_ceiling"] < 0.95
