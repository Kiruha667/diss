"""Generate notebooks/demo.ipynb (DEMO_COLAB.md, step 3). Run from the repository root after build_demo_data.py:
    python notebooks/make_demo_notebook.py
The expected SHA256 of the data files are read from data/demo/SHA256SUMS.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMS = dict(reversed(line.split()) for line in (ROOT / "data/demo/SHA256SUMS").read_text().splitlines() if line)

CELLS: list[tuple[str, str]] = []


def md(text: str) -> None:
    CELLS.append(("markdown", text.strip("\n")))


def code(text: str) -> None:
    CELLS.append(("code", text.strip("\n")))


md("""
# Оценка достоверности выполнения задач LLM-агентами по структуре траектории

Демонстрация к магистерской работе «Разработка модели оценки достоверности выполнения задач LLM-агентами
на основе анализа структуры траекторий выполнения».

**Достоверное выполнение** — агент отправил результат (submit), и скрытые тесты SWE-bench подтверждают, что задача
решена. Модель по ходу работы агента (шагам, командам, их фазам) оценивает **риск**: вероятность, что отправленный
результат тесты не пройдёт.

Запуск: *Runtime → Run all* на чистом рантайме, около 2–3 минут. Ключи API, вызовы LLM и Docker не нужны;
все случайные операции — с `seed = 42`.
""")

md("""
## 1. Установка и загрузка

Ставится одна библиотека с закреплённой версией (scikit-learn — от неё зависят числа логистической регрессии);
numpy, pandas, pyarrow, scipy и matplotlib берутся из рантайма Colab, их версии печатаются ниже. Данные скачиваются
из релиза репозитория по прямым ссылкам, SHA256 каждого файла сверяется с ожидаемым.
""")

code("""
%pip install -q scikit-learn==1.9.1
""")

code(f"""
import hashlib, json, os, sys, time, urllib.error, urllib.request, zipfile
from pathlib import Path

T0 = time.time()
SEED = 42
REPO = "Kiruha667/diss"
TAG = "demo-data-v1"
# Files of the GitHub release TAG; DEMO_BASE_URL overrides the location (used for the local test run).
BASE_URL = os.environ.get("DEMO_BASE_URL", f"https://github.com/{{REPO}}/releases/download/{{TAG}}")
EXPECTED_SHA256 = {json.dumps({k: SUMS[k] for k in ("runs.parquet", "steps.parquet", "examples.json", "demo_lib.zip")}, indent=4)}
DATA = Path("demo_data")
DATA.mkdir(exist_ok=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, path, attempts=3):
    # streamed download with a timeout and retries (transient network errors)
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(path, "wb") as fh:
                while chunk := r.read(1 << 20):
                    fh.write(chunk)
            return
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"cannot download {{url}} (HTTP {{e.code}}): the release must be public") from None
        except OSError as e:
            if i == attempts - 1:
                raise
            print(f"retry {{i + 1}} for {{url}}: {{e}}")
            time.sleep(3)


for name, expected in EXPECTED_SHA256.items():
    path = DATA / name
    if not path.exists() or sha256(path) != expected:
        download(f"{{BASE_URL}}/{{name}}", path)
    got = sha256(path)
    assert got == expected, f"SHA256 mismatch for {{name}}: {{got}}"
    print(f"{{name:14s}} {{path.stat().st_size / 1e6:7.1f}} MB   SHA256 OK")

with zipfile.ZipFile(DATA / "demo_lib.zip") as z:
    z.extractall(DATA)
sys.path.insert(0, str(DATA.resolve()))
""")

code("""
import math
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow
import pyarrow.parquet as pq
import scipy
import sklearn
from IPython.display import Markdown, display
from scipy.stats import rankdata
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from demo_lib.features import FEATURES, LEN, PHASE_CODES, step_phases, steps_from_frame, trajectory_features

np.random.seed(SEED)
pd.set_option("display.max_colwidth", 160)
pd.set_option("display.width", 200)
print("python", sys.version.split()[0], "| numpy", np.__version__, "| pandas", pd.__version__, "| pyarrow",
      pyarrow.__version__, "| scipy", scipy.__version__, "| scikit-learn", sklearn.__version__, "| matplotlib",
      matplotlib.__version__)

runs = pd.read_parquet(DATA / "runs.parquet")
examples = json.loads((DATA / "examples.json").read_text(encoding="utf-8"))
steps_file = pq.ParquetFile(DATA / "steps.parquet")
# Only the steps of the example runs are needed below; read them with a filter instead of all rows.
example_ids = [e["run_id"] for e in examples]
steps = pq.read_table(DATA / "steps.parquet", filters=[("run_id", "in", example_ids)]).to_pandas()
print("runs.parquet:", runs.shape, "| steps.parquet:", steps_file.metadata.num_rows, "rows,",
      steps_file.metadata.num_columns, "columns | steps of the examples:", len(steps))
""")

md("""
## 2. Обзор датасета

Одна строка `runs` — один прогон агента mini-SWE-agent на одной задаче SWE-bench Verified. `resolved` — прошли ли
скрытые тесты; признаки модели (последние колонки) посчитаны кодом пайплайна по всей траектории.
""")

code("""
print("runs.shape =", runs.shape)
display(runs.dtypes.to_frame("dtype").T)
display(runs.head())
""")

code("""
both = runs.groupby("task_id").resolved.nunique().eq(2)
print(f"resolved share: {runs.resolved.mean():.3f}   tasks: {runs.task_id.nunique()}   "
      f"models: {runs.model.nunique()}   families: {runs.model_family.nunique()}   "
      f"agent versions: {runs.agent_version.nunique()}")
print(f"tasks with both resolved and unresolved runs: {int(both.sum())}")
by_model = (runs.groupby(["model", "model_family", "agent_version"])
            .agg(runs=("run_id", "size"), resolved_share=("resolved", "mean"))
            .round(3).sort_values("runs", ascending=False))
display(by_model)
display(runs.exit_status.value_counts().to_frame("runs"))
""")

md("""
## 3. Популяция

Оценивается только то, что агент **отправил**: `submitted == 1` (статус выхода `Submitted`). Прогоны, оборвавшиеся
по лимиту или ошибке, результата не сдали — оценивать там нечего, поэтому они исключены. Все дальнейшие расчёты — на
этой популяции. Целевая переменная — `1 − resolved` (задача не решена), риск модели — её вероятность.
""")

code("""
pop = runs[runs.submitted == 1].sort_values("run_id").reset_index(drop=True)
excluded = runs[runs.submitted == 0]
print(f"population (submitted == 1): {len(pop)} runs, {pop.task_id.nunique()} tasks; excluded: {len(excluded)}")
display(excluded.exit_status.value_counts().to_frame("excluded runs"))
y = (1 - pop.resolved).to_numpy()
print(f"unresolved share in the population: {y.mean():.3f}")
""")

md("""
## 4. Примеры траекторий

Каждый шаг агента размечен одной из трёх фаз — разметка существующего кода (правила G0/E5 над выполненными
bash-командами). Коды в `phase_seq`:

| код | фаза | что это |
|---|---|---|
| `E` | edit | шаг правит код: `sed -i`, запись в файл через `>`/heredoc/`tee`, `git apply`, `patch`, запись из python |
| `V` | verify | шаг запускает проверку: pytest / tox / `python -m unittest`, скрипт тестов, python-код с `assert` |
| `O` | other | всё остальное: чтение файлов, поиск, запуск без проверки, служебные команды |

Если в шаге есть и правка, и проверка, побеждает правка. Примеры отобраны детерминированно (seed 42), предпочтительно
длиной 15–40 шагов.
""")

code("""
LEGEND = {"E": "edit", "V": "verify", "O": "other"}
TYPE_NAMES = {"pair_resolved": "пара внутри задачи: решено", "pair_unresolved": "пара внутри задачи: не решено",
              "false_claim": "ложное заявление об успехе", "verified_success": "достоверное выполнение с проверкой",
              "not_submitted": "обрыв без отправки"}
runs_by_id = runs.set_index("run_id")


def show_meta(run_id):
    r = runs_by_id.loc[run_id]
    return pd.DataFrame([{"task_id": r.task_id, "model": r.model, "agent_version": r.agent_version,
                          "n_steps": r.n_steps, "exit_status": r.exit_status, "submitted": r.submitted,
                          "claim_success": r.claim_success, "resolved": r.resolved}], index=[run_id])


def show_steps(run_id):
    s = steps[steps.run_id == run_id].sort_values("step_idx")
    return pd.DataFrame({"step_idx": s.step_idx, "phase": s.phase, "command": s.command,
                         "output (150)": s.output.str.slice(0, 150)}).set_index("step_idx")


def show_example(e):
    display(Markdown(f"### {TYPE_NAMES[e['type']]}\\n`{e['run_id']}`"))
    display(show_meta(e["run_id"]))
    print("phase_seq:", runs_by_id.loc[e["run_id"], "phase_seq"])
    display(show_steps(e["run_id"]))
    print("final_message:", runs_by_id.loc[e["run_id"], "final_message"] or "<no final message>")


pair = [e for e in examples if e["type"].startswith("pair_")]
display(Markdown(f"### Пара внутри одной задачи `{pair[0]['task_id']}`: две последовательности фаз"))
for e in pair:
    r = runs_by_id.loc[e["run_id"]]
    print(f"{TYPE_NAMES[e['type']]:30s} {r.model:28s} resolved={r.resolved}  {r.phase_seq}")
""")

code("""
for e in pair:
    show_example(e)
""")

code("""
for e in examples:
    if not e["type"].startswith("pair_"):
        show_example(e)
""")

md("""
## 5. От траектории к признакам

Функция `trajectory_features` из `demo_lib` (копия логики пайплайна) по шагам одного прогона заново размечает фазы
и считает вектор признаков модели `M_struct+len`. Справа — значение, сохранённое в `runs.parquet` старым кодом:
они должны совпасть.
""")

code("""
DESCR = {
    "n_steps": "число шагов агента", "seg_len_steps": "шагов после последней правки",
    "seg_len_sent": "предложений рассуждения после последней правки", "n_edits": "шагов с правкой",
    "has_any_verification": "была ли хоть одна проверка", "n_verifications": "шагов с проверкой",
    "verif_after_last_edit": "есть проверка после последней правки",
    "n_verif_after_last_edit": "проверок после последней правки",
    "verified_before_first_edit": "проверка до первой правки",
    "n_steps_after_last_edit": "шагов после последней правки (дубль seg_len_steps)",
    "share_edit": "доля шагов-правок", "share_verify": "доля шагов-проверок", "share_other": "доля прочих шагов",
    "n_transitions": "смен фазы между соседними шагами", "phase_bigram_entropy": "энтропия биграмм фаз (норм.)",
    "share_before_first_edit": "доля шагов до первой правки", "repeat_ratio": "доля повторов команды подряд",
    "distinct_cmd_ratio": "уникальных команд на шаг", "nonzero_rc_share": "доля выводов с ненулевым кодом возврата",
    "n_files_edited": "разных файлов правилось", "n_file_switches": "переключений между файлами правок"}
ex = next(e for e in examples if e["type"] == "false_claim")
st = steps_from_frame(steps[steps.run_id == ex["run_id"]])
feat = trajectory_features(st)
table = pd.DataFrame({"value (demo_lib)": [feat[c] for c in FEATURES],
                      "value (runs.parquet)": [runs_by_id.loc[ex["run_id"], c] for c in FEATURES],
                      "meaning": [DESCR[c] for c in FEATURES]}, index=FEATURES)
print("run:", ex["run_id"])
print("relabelled phases:", " ".join(PHASE_CODES[p] for p in step_phases(st)))
display(table.round(4))
print("max |difference|:", float((table.iloc[:, 0] - table.iloc[:, 1]).abs().max()))
""")

md("""
## 6. Обучение и оценка

Две модели: `M_len` — только длина (4 признака), `M_struct+len` — все 21 признак E5. Все признаки процессные:
считаются по шагам, командам и выводам среды, не по отправленному патчу и не по тестам. Логистическая регрессия с L2
и стандартизацией, без подбора гиперпараметров; `GroupKFold(5)` по `task_id` — задача не попадает одновременно в
обучение и тест; риск — out-of-fold. Интервалы — кластерный бутстрэп по задачам, 1000 повторов.
""")

code("""
MODELS = {"M_len": LEN, "M_struct+len": FEATURES}
groups = pop.task_id.to_numpy()
fold = np.full(len(pop), -1)
for k, (_, te) in enumerate(GroupKFold(n_splits=5, shuffle=True, random_state=SEED).split(pop, y, groups)):
    fold[te] = k


def new_model():
    return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=5000))


def oof_risk(cols):
    risk = np.zeros(len(pop))
    for k in range(5):
        tr, te = fold != k, fold == k
        assert not set(groups[tr]) & set(groups[te])  # no task in both train and test
        m = new_model().fit(pop.loc[tr, cols].to_numpy(float), y[tr])
        risk[te] = m.predict_proba(pop.loc[te, cols].to_numpy(float))[:, 1]
    return risk


def auroc(yy, ss):
    r = rankdata(ss)
    n1 = yy.sum()
    n0 = len(yy) - n1
    return float((r[yy == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


risk = {name: oof_risk(cols) for name, cols in MODELS.items()}

# cluster bootstrap over tasks: the same resamples for both models
rng = np.random.default_rng(SEED)
task_rows = list(pd.Series(np.arange(len(pop))).groupby(groups).apply(np.array))
boot_idx = [np.concatenate([task_rows[i] for i in rng.integers(0, len(task_rows), len(task_rows))])
            for _ in range(1000)]
boot = {name: np.array([auroc(y[ix], r[ix]) for ix in boot_idx]) for name, r in risk.items()}

# within-task: all (unresolved, resolved) pairs of one task, pooled over tasks
pair_tasks = [ix for ix in task_rows if 0 < y[ix].sum() < len(ix)]


def pair_counts(r):
    conc, n = [], []
    for ix in pair_tasks:
        u, s = r[ix][y[ix] == 1], r[ix][y[ix] == 0]
        d = u[:, None] - s[None, :]
        conc.append((d > 0).sum() + 0.5 * (d == 0).sum())
        n.append(d.size)
    return np.array(conc), np.array(n)


within, wboot = {}, {}
rng_w = np.random.default_rng(SEED)
wsamples = [rng_w.integers(0, len(pair_tasks), len(pair_tasks)) for _ in range(1000)]
for name, r in risk.items():
    c, n = pair_counts(r)
    within[name] = c.sum() / n.sum()
    wboot[name] = np.array([c[s].sum() / n[s].sum() for s in wsamples])
n_pairs = int(pair_counts(risk["M_len"])[1].sum())

ci = lambda v: np.percentile(v, [2.5, 97.5])  # noqa: E731
rows = []
for name in MODELS:
    lo, hi = ci(boot[name])
    wlo, whi = ci(wboot[name])
    fa = np.mean([auroc(y[fold == k], risk[name][fold == k]) for k in range(5)])
    rows.append({"model": name, "AUROC": auroc(y, risk[name]), "95% CI": f"[{lo:.3f}; {hi:.3f}]",
                 "AUROC within tasks": within[name], "95% CI within": f"[{wlo:.3f}; {whi:.3f}]",
                 "fold-averaged AUROC (check)": fa})
results = pd.DataFrame(rows).set_index("model").round(3)
display(results)
d = auroc(y, risk["M_struct+len"]) - auroc(y, risk["M_len"])
dlo, dhi = ci(boot["M_struct+len"] - boot["M_len"])
wd = within["M_struct+len"] - within["M_len"]
wdlo, wdhi = ci(wboot["M_struct+len"] - wboot["M_len"])
print(f"difference M_struct+len - M_len: AUROC {d:+.3f} [{dlo:+.3f}; {dhi:+.3f}]; "
      f"within tasks {wd:+.3f} [{wdlo:+.3f}; {wdhi:+.3f}]")
print(f"within-task pairs: {len(pair_tasks)} tasks with both outcomes, {n_pairs} (unresolved, resolved) pairs; "
      f"share of pairs where the unresolved run got the higher risk = within-task AUROC")
""")

code("""
fig, ax = plt.subplots(figsize=(6.4, 3.8))
modes = [("AUROC", boot, "по всей популяции"), ("AUROC within tasks", wboot, "внутри задач")]
w = 0.36
for j, name in enumerate(MODELS):
    vals = [results.loc[name, m] for m, _, _ in modes]
    errs = np.array([[results.loc[name, m] - ci(b[name])[0], ci(b[name])[1] - results.loc[name, m]]
                     for m, b, _ in modes]).T
    ax.bar(np.arange(2) + (j - 0.5) * w, vals, w, yerr=errs, capsize=4, label=name)
ax.axhline(0.5, color="grey", lw=0.8, ls="--")
ax.set_xticks(np.arange(2), [lbl for _, _, lbl in modes])
ax.set_ylim(0.4, 0.85)
ax.set_ylabel("AUROC (out-of-fold)")
ax.set_title("Риск нерешённой задачи: AUROC с 95% интервалами", fontsize=10)
ax.legend()
plt.tight_layout()
plt.show()
""")

md("""
## 7. Практическая полезность

Если проверять вручную только самые подозрительные прогоны: бюджет k — доля популяции с наибольшим риском.
precision@k — доля нерешённых среди отобранных, lift@k — во сколько раз она выше, чем при случайном выборе.
""")

code("""
util = []
base = y.mean()
for frac in (0.05, 0.10, 0.20):
    k = math.ceil(frac * len(pop))
    for name in MODELS:
        top = np.argsort(-risk[name], kind="mergesort")[:k]
        p = y[top].mean()
        util.append({"budget": f"{frac:.0%}", "checked": k, "model": name, "unresolved found": int(y[top].sum()),
                     "precision@k": round(p, 3), "lift@k": round(p / base, 2)})
utility = pd.DataFrame(util).set_index(["budget", "checked", "model"])
print(f"base rate of unresolved runs: {base:.3f}")
display(utility)
""")

md("""
## 8. Ранжирование

Десять прогонов с наибольшим риском `M_struct+len` (out-of-fold: модель не видела задачу прогона при обучении).
""")

code("""
top10 = pop.assign(risk=risk["M_struct+len"]).nlargest(10, "risk")[["task_id", "model", "n_steps", "risk",
                                                                     "resolved"]]
display(top10.round(3))
""")

md("""
## 9. Оценка одной траектории

`score_trajectory` получает только шаги прогона, сама размечает фазы по командам, считает признаки и возвращает риск.
Модель для каждого примера обучается на популяции **без задачи этого примера**, так что задача в обучении не
встречалась. Пример «обрыв без отправки» лежит вне популяции — его риск показан только для иллюстрации.
""")

code("""
_models = {}


def score_trajectory(steps_df):
    \"\"\"Risk that the submitted result does not pass the hidden tests, from the steps of one run.\"\"\"
    run_id = steps_df.run_id.iloc[0]
    task = runs_by_id.loc[run_id, "task_id"]
    if task not in _models:  # demo model trained on every task except this one
        train = pop[pop.task_id != task]
        _models[task] = new_model().fit(train[FEATURES].to_numpy(float), 1 - train.resolved.to_numpy())
    f = trajectory_features(steps_from_frame(steps_df))  # phases are relabelled from the commands here
    x = np.array([[f[c] for c in FEATURES]], dtype=float)
    return float(_models[task].predict_proba(x)[0, 1])


scored = []
for e in examples:
    s = steps[steps.run_id == e["run_id"]]
    r = runs_by_id.loc[e["run_id"]]
    assert r.task_id not in set(pop[pop.task_id != r.task_id].task_id)
    scored.append({"example": TYPE_NAMES[e["type"]], "task_id": r.task_id, "model": r.model,
                   "submitted": int(r.submitted), "risk": round(score_trajectory(s), 3), "resolved": int(r.resolved)})
display(pd.DataFrame(scored))
""")

md("""
## 10. Ограничения

1. Только SWE-bench Verified и один каркас агента (mini-SWE-agent, режим bash-only); перенос на другие домены
   отдельно проверялся в работе и не подтвердился.
2. Метка — скрытые тесты бенчмарка: «решено» значит «прошли тесты SWE-bench», а не ручная проверка правильности.
3. `claim_success` — детектор на регулярных выражениях (G0); заявления, сформулированные иначе, он пропускает.
4. Признаки могут частично отражать идентичность модели (её манеру работы), а не только качество конкретного прогона.
5. Числа отличаются от прежних: в E5 модель обучалась на подвыборке 2877 траекторий с заявлением об успехе, здесь —
   на всех отправленных прогонах, и AUROC считается по объединённым out-of-fold предсказаниям.
""")

code("""
summary = {"population": len(pop), "excluded": int(len(excluded)),
           "auroc": {m: float(auroc(y, risk[m])) for m in MODELS},
           "auroc_ci": {m: [float(v) for v in ci(boot[m])] for m in MODELS},
           "auroc_diff": [float(d), float(dlo), float(dhi)],
           "within": {m: float(within[m]) for m in MODELS},
           "within_ci": {m: [float(v) for v in ci(wboot[m])] for m in MODELS},
           "within_diff": [float(wd), float(wdlo), float(wdhi)],
           "within_tasks": len(pair_tasks), "within_pairs": n_pairs,
           "fold_avg": {m: float(results.loc[m, "fold-averaged AUROC (check)"]) for m in MODELS},
           "utility": utility.reset_index().to_dict("records"), "scored": scored,
           "elapsed_s": round(time.time() - T0, 1)}
Path("demo_results.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False,
                                                default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                                     encoding="utf-8")
print(f"done in {time.time() - T0:.0f} s (after pip install)")
""")


def to_ipynb(cells) -> dict:
    out = []
    for i, (kind, src) in enumerate(cells):
        lines = src.split("\n")
        source = [ln + "\n" for ln in lines[:-1]] + [lines[-1]]
        cell = {"cell_type": kind, "id": f"cell-{i:02d}", "metadata": {}, "source": source}
        if kind == "code":
            cell.update({"execution_count": None, "outputs": []})
        out.append(cell)
    return {"cells": out, "nbformat": 4, "nbformat_minor": 5,
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                         "language_info": {"name": "python"}, "colab": {"provenance": []}}}


if __name__ == "__main__":
    (ROOT / "notebooks" / "demo.ipynb").write_text(json.dumps(to_ipynb(CELLS), indent=1, ensure_ascii=False) + "\n",
                                                   encoding="utf-8")
    print("notebooks/demo.ipynb written,", len(CELLS), "cells")
