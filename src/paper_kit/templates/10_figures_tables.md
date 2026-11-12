# 10. Рисунки и таблицы

Рисунки строит `python -m src.paper_figures` только из `outputs/` (без обучения моделей и вызовов API) в
`paper_kit/figures/` (PDF для LaTeX, PNG для просмотра). Цвета — фиксированные слоты эталонной категориальной палитры
метода dataviz; потолки и базы помечены штриховкой и подписями, поэтому рисунки читаются в оттенках серого.

## Рисунки

### Рисунок 1 — отбор на проверку (`figures/fig1_triage.pdf`)

- **Что показывает:** precision@10% моделей на лидерборде без пустых патчей с доверительными интервалами; пунктир — доля нерешённых;
  штрихованный столбец — потолок «сложность задачи по другим моделям».
- **Данные:** `outputs/e15_leaderboard.csv` (популяция P_ne, метрика p@10), доля нерешённых —
  `outputs/e15_summary.json`.
- **Подпись (EN):** "Precision@10% (review budget of one tenth of the runs) on submitted runs without empty patches. Model identity plus
  length (M + length) matches the trajectory model (M+S); task difficulty estimated from other models' outcomes is a
  ceiling, not a deployable predictor. Dashed line: base rate of unresolved runs ({{e15.ne.base|v}}). Error bars: cluster-bootstrap
  confidence intervals over tasks." {{src:e15.ne.base}}

### Рисунок 2 — уровень попытки (`figures/fig2_run_level.pdf`)

- **Что показывает:** AUROC внутри единицы (одна модель, одна задача) на всех парах и на парах законченных попыток для
  базы «попытка не закончена», `S` (внутри настройки и на двух вместе), `S+O`; панели — devstral-2512 и обе настройки.
- **Данные:** `outputs/e15_kth_within.csv`.
- **Подпись (EN):** "Within-unit AUROC on repeated runs (ASSERT-KTH, two settings). On all pairs the structure model
  is close to the single flag 'attempt not finished'; among pairs of finished attempts it is not distinguishable from
  chance. Only the agent's own check outcomes keep a small signal for devstral-2512 ({{e15k.dev.so2.ff|v}} on finished
  pairs). Error bars: bootstrap confidence intervals over units." {{src:e15k.dev.so2.ff}}

### Рисунок 3 — разложение дисперсии (`figures/fig3_variance.pdf`)

- **Что показывает:** доли дисперсии исхода на латентной шкале: задача, модель, задача × модель, попытка.
- **Данные:** `outputs/e15_variance.csv` (популяция E8, две настройки, латентная шкала).
- **Подпись (EN):** "Latent variance of the outcome on repeated runs (mixed logistic model, two settings): task
  {{e15v.lat.task.pt|v}}, attempt {{e15v.lat.attempt.pt|v}}. Identical patches almost never receive different labels,
  so the within-unit AUROC ceiling is about one." {{src:e15v.lat.task.pt}} {{src:e15v.lat.attempt.pt}}

### Рисунок 4 — ответы на обычный вопрос (`figures/fig4_self_assessment.pdf`)

- **Что показывает:** распределение ответов C3 у решённых и нерешённых законченных попыток devstral-2512 (выборка E16).
- **Данные:** `outputs/e16_attempts.csv` (`C3_number`, `label`).
- **Подпись (EN):** "Answers of devstral-2512 to 'probability that the hidden tests pass', asked in its own context
  after a finished attempt. Answers concentrate at the top of the scale for both outcomes; within-unit AUROC
  {{e16.all|v}}." {{src:e16.all}}

## Таблицы

| № | таблица | содержание | где взять |
|---|---|---|---|
| Table I | Main decomposition | `D` (потолок), `M`, `M` + log шагов, `M` + длина, `S`, `M+S`; общий AUROC, внутри задачи, precision@10%, recall@10%; P_ne | `05_results_decomposition.md`, §1 |
| Table II | Attempt level | devstral-2512: база «не закончена», `S` (T1, T2), `S+O`; все пары и пары законченных; строка «обе настройки» | `06_results_run_level.md`, §2 |
| Table III | Agent's words | G0 после поправки (строгий, широкий); C4 (E11b), C3 (E16: все и новые пары); разности с `S+O` и G0 | `07_results_agent_words.md` |
| Table IV | Positioning | 2611.08285 и 2610.05572 против нашей работы | `02_related_work.md`, §1 |

Для диссертации дополнительно: таблица переноса (`08_transfer_limitations.md`, §1) и таблица выбора из k
(`06_results_run_level.md`, §3).
