# INVENTORY — что лежит в репозитории

Состояние `main` на 2026-11-10.

**Статусы:**
- **актуально** — используется для текущей постановки или является её результатом;
- **устарело** — относится к прежней постановке, указано, чем заменено;
- **вспомогательное** — служебные файлы, журналы, генераторы.

**Этапы:**
- G0 — самоотчёт агента;
- итерация 1 (E0–E4) — хаотичность семантических траекторий;
- итерация 2 (E5–E6) — «достоверность утверждений» на подвыборке 2 877;
- демо — утверждённая постановка, 7 864 прогона;
- E7–E14;
- собственный корпус.

Все этапы — в `docs/EXPERIMENTS.md`.

Решения автора по спорным файлам (2026-11-10): `AGENTS.md` (задание итерации 1) перенесён в `archive/`; материалы E5 оставлены на месте.

## Верхний уровень

| путь | что это | этап | статус |
|---|---|---|---|
| `README.md` | короткий вход в репозиторий (переписан в этой ветке; прежний — `archive/README_2026-10.md`) | все | актуально |
| `REVIEW_PACKET.md` | пакет для независимого рецензента (части A и B) | все | актуально (новое) |
| `INVENTORY.md` | этот файл | — | актуально (новое) |
| `CLEANUP_FINDINGS.md`, `CLEANUP_REPORT.md` | находки уборки и отчёт о ней | — | актуально (новое) |
| `DATASET_CARD.md` | источники данных, ревизии, даты снапшотов, SHA256 | все | актуально (новое) |
| `docs/` | `PROJECT_DOCUMENTATION.md` (полная документация v3, перенесена из корня), `EXPERIMENTS.md`, `LITERATURE.md`, `REPRODUCE.md`, `checksums/` | все | актуально |
| `archive/` | устаревшие материалы с причинами (`archive/README.md`): E6, прежний README, задание итерации 1 (`AGENTS_iteration1.md`) | итерации 1–2 | устарело |
| `AGENTS.md`, `CLAUDE.md` | одинаковые короткие файлы для агентных инструментов: навигация и правила (без результатов) | все | актуально |
| `pytest.ini` | `testpaths = tests` | — | вспомогательное |
| `G0_PROMPT.md` | спецификация G0 | G0 | актуально (результаты G0 используются; на файл ссылаются `src/g0_false_success.py`, `src/cli.py`, `config/*_patterns.txt`) |
| `E5_PROMPT.md` | спецификация E5 (подвыборка 2 877 заявивших успех) | итерация 2 | старая постановка, но определение признаков S актуально (`src/e5_claims.py`); оставлено на месте |
| `DEMO_COLAB.md` | спецификация демонстрационного ноутбука | демо | актуально |
| `DEMO_REPORT.md` | отчёт демо: инвентаризация данных, S 0.662 / 0.640 | демо | актуально |
| `VISUAL_DEMO_REPORT.md` | отчёт наглядного ноутбука | демо, E7, E8 | актуально (E9–E14 в ноутбук не внесены) |
| `OWN_DATASET_PLAN.md` | план собственного корпуса | собственный корпус | актуально |
| `requirements.txt` | закреплённые версии, 86 пакетов | все | актуально |
| `.gitignore` | исключения из репозитория | — | вспомогательное |
| `config/` | шаблоны детекторов, описание источников | см. ниже | — |
| `data/` | в git только `data/demo/` | демо | см. ниже |
| `demo_lib/` | `features.py` (21 признак `S`), `phases.py` (фазы edit / verify / other) — дословный перенос логики `src/e5_claims` | демо, E7–E14 | актуально |
| `kaggle/` | `build/`, `pulled/` — ядра Kaggle E0, E1, E3, G0; в git не входит (`.gitignore`) | итерация 1, G0 | вспомогательное |
| `notebooks/` | ноутбуки и генераторы | демо | см. ниже |
| `outputs/` | результаты всех этапов | все | см. ниже |
| `own_data/` | собственный корпус | собственный корпус | см. ниже |
| `reports/` | протоколы и отчёты | все | см. ниже |
| `src/` | код | все | см. ниже |
| `tests/` | модульные тесты (`python -m pytest tests`: 36 passed) | итерация 1–2, G0 | см. ниже |
| `.venv/`, `.pytest_cache/` | локальное окружение и кэш, в git не входят | — | вспомогательное |

## data/ (в git)

| путь | что это | статус |
|---|---|---|
| `data/demo/runs.parquet` | одна строка на прогон: 7 975, из них отправлено 7 864; метки, связка, 21 признак `S` | актуально (вход E7–E14 и демо) |
| `data/demo/examples.json`, `checks.json`, `SHA256SUMS`, `DATASET_CARD.md` | примеры для ноутбука, проверки сборки, хеши, карточка демо-данных | актуально |

## reports/

| файл | этап | статус |
|---|---|---|
| `E0_inventory.md` | E0 | актуально (описание корпуса) |
| `E1_embedding_log.md`, `E1_segmentation.md` | итерация 1 | актуально как отрицательный результат итерации 1 |
| `E2_projections.md`, `E2_projections_nomic.md` | итерация 1 | то же |
| `E4_results.md`, `E4_results_nomic.md`, `E4_encoder_check.md`, `E4_encoder_check.csv` | итерация 1 | то же; главный ответ итерации 1 (Δ 0.014 [−0.004; 0.031]) |
| `E4_smoke_mahmoud.md` | итерация 1, проверка пайплайна на чужих эмбеддингах | вспомогательное |
| `G0_false_success.md` | G0 | актуально; детектор без проверки человеком (`CLEANUP_FINDINGS.md` 2.6) |
| `G0_manual_review.md` | G0 | актуально; проверка выполнена ИИ, а не человеком |
| `G0_HUMAN_CHECK_PROTOCOL.md`, `G0_HUMAN_CHECK.md` | проверка детектора G0 по разметке 100 сообщений (метки модели, просмотренные автором; разделы 9–10 протокола) | актуально |
| `E5_results.md`, `E5_segment_audit.md` | итерация 2 | старая постановка, но определение признаков S актуально (`src/e5_claims.py`); не перенесено |
| `E7_PROTOCOL.md`, `E7_REPORT.md` | E7 (+ дополнение, раздел 9) | актуально |
| `E8_PROTOCOL.md`, `E8_REPORT.md`, `E8_adapter_check.md` | E8 | актуально |
| `E9_PROTOCOL.md`, `E9_REPORT.md` | E9 | актуально |
| `E10_PROTOCOL.md`, `E10_REPORT.md`, `E10_parser_check.md` | E10 | актуально |
| `E11_PROTOCOL.md` (разделы 1–13, включая E11b), `E11_REPORT.md`, `E11b_REPORT.md` | E11, E11b | актуально; C1/C2 предварительные до ручной разметки |
| `E12_PROTOCOL.md`, `E12_REPORT.md` | E12 | актуально |
| `E13_PROTOCOL.md`, `E13_REPORT.md` | E13 | актуально |
| `E14_PROTOCOL.md`, `E14_REPORT.md` | E14 | актуально |
| `figures/e5_reliability.png` | итерация 2 | как `E5_results.md` |
| `figures/e9_best_of_k.png` | E9 | актуально |
| `figures/early_curve_main.png`, `early_curve_main_nomic.png`, `early_curve_smoke_mahmoud.png` | итерация 1 | актуально как отрицательный результат |
| `kaggle_env_e0.txt`, `kaggle_env_e1.txt`, `kaggle_env_e3.txt` | окружения ядер Kaggle | вспомогательное |
| `E6_*.md`, `figures/e6_*.png` | итерация 2 | **устарело** → перенесено в `archive/old_formulation/reports/` |

## src/

| файл | этап | статус |
|---|---|---|
| `__init__.py`, `common.py` | все | актуально (пути, seed, общие функции) |
| `cli.py` | точка входа E0–E6, G0 (`python -m src.cli …`) | актуально; справка `e6` ссылается на старый путь `E6_PROMPT.md` |
| `inventory.py` | E0 | актуально |
| `adapters/swebench_bashonly.py` | E0; разбор корпуса и новых прогонов (E14) | актуально |
| `adapters/agentic_evals.py` | ASSERT-KTH (E8–E11, E13) | актуально |
| `adapters/exgentic.py` | Exgentic (E6, блок 4) | устарело (E6 в архиве) |
| `adapters/mahmoud_vectors.py` | проверка пайплайна E4 | вспомогательное |
| `segment.py` | E1; также используется сборкой демо-данных | актуально |
| `embed.py` | E1; `load_encoder` используется в E12 | актуально |
| `project.py`, `chaos.py`, `features.py`, `folds.py`, `stats.py` | итерация 1 (проекции, ordpy, признаки E3, фолды, тесты E4) | актуально как код отрицательного результата итерации 1; дальше не используется (кроме `outputs/folds.csv` в E5 и E6) |
| `kaggle_job.py` | запуск E0, E1, E3 на Kaggle | вспомогательное |
| `g0_false_success.py` | G0; детектор используется в E7, E10, E11 и ручной проверке | актуально |
| `g0_human_check.py` | проверка детектора G0 по разметке | актуально |
| `g0_mutual_information.py` | взаимная информация «заявление детектора × исход» | актуально |
| `e5_claims.py` | E5; `process_record` строит признаки `S` (демо-данные, E14) | актуально (модуль), постановка E5 устарела |
| `e6_refine.py`, `e6_external.py` | E6 | устарело (итоговая модель — `M+S`); код не перенесён: на него ссылаются `src/cli.py` и тесты |
| `e7_sources.py`, `e7_addendum.py` | E7 | актуально |
| `e8_repeats.py` | E8 (с 12.11.2026 — две настройки KTH; `KTH_WITH_DEEPSWE=1` — прежний расчёт) | актуально |
| `e9_best_of_k.py` | E9 | актуально |
| `e10_check_outcomes.py`, `e10_block_o.py` | E10 | актуально |
| `e11_elicitation.py` | E11 | актуально |
| `e11b_finished_pairs.py` | E11b | актуально |
| `e12_task_difficulty.py` | E12 | актуально |
| `e13_generations_patch.py` | E13 | актуально |
| `e14_new_models.py` | E14 | актуально |
| `e15_consolidation.py` | E15 (части 2, 3 — две настройки KTH) | актуально |
| `e16_c3_confirm.py` | E16 | актуально |
| `kth_deepswe_labels.py` | доказательства по меткам DeepSWE-Preview в KTH (`reports/KTH_DEEPSWE_LABELS.md`) | актуально |
| `paper_figures.py`, `paper_kit/` | рисунки и сборка набора для статьи `paper_kit/` (шаблоны, индекс чисел, проверка) | актуально |

## notebooks/

| файл | что это | статус |
|---|---|---|
| `demo.ipynb` | технический демо-ноутбук для Colab: данные, признаки, обучение `S` | актуально |
| `visual_demo.ipynb` | наглядный ноутбук; через `assert` воспроизводит `M+S` 0.685 / 0.689 | актуально; E9–E14 не внесены |
| `build_demo_data.py` | сборка `data/demo/` из корпуса; `record_rows` используется в E14 | актуально |
| `make_demo_notebook.py`, `make_visual_demo.py`, `make_demo_docs.py` | генераторы ноутбуков и документации демо | вспомогательное |
| `demo_report_notes.md` | заметки к `DEMO_REPORT.md` (проверка в Colab, версии) | вспомогательное |

## config/

| файл | этап | статус |
|---|---|---|
| `datasets.yaml` | все: источники, 16 прогонов D2, правила исключения | актуально |
| `claim_patterns.txt`, `admit_patterns.txt` | G0 (заморожены, коммит 4c88e83) | актуально; 6 из 9 шаблонов признания не срабатывают ни разу (`reports/G0_false_success.md`) |
| `claim_patterns_domain.txt` | E6, блок 4: заявления в диалоговом домене | устарело (E6) |
| `leak_markers.txt`, `leak_marker_candidates.txt` | E0 и E1: маркеры исхода; используются сборкой демо-данных | актуально |
| `e11_admit_patterns.txt`, `e11_neutral_patterns.txt` | E11: флаг C1/C2 | актуально |

## tests/

| файл | что проверяет | статус |
|---|---|---|
| `test_pipeline.py` | E0–E4: адаптер, сегментация, признаки, статистика | актуально (итерация 1) |
| `test_g0.py` | G0 | актуально |
| `test_e5.py` | E5 (признаки `S`) | актуально |
| `test_e6.py`, `test_exgentic.py` | E6 и адаптер Exgentic | устарело (E6), тесты проходят |

Тестов для E7–E14 нет; в коде этих этапов стоят проверки воспроизведения с остановкой (`CLEANUP_FINDINGS.md` 3.3).

## outputs/ (113 файлов)

| файлы | этап | статус |
|---|---|---|
| `features.csv.gz`, `features_nomic.csv`, `summary.csv`, `summary_main_nomic.csv`, `transfer_main*.csv`, `early_curve_main*.csv`, `nested_choices_*.csv`, `nested_predictions_*.csv`, `e2_ljungbox*.csv`, `e1_pilot_runs.csv`, `segment_stats.csv`, `window_exclusions*.csv`, `exclusions.csv`, `leak_markers_freq.csv`, `nomic_subset.csv`, `traj_index.csv`, `traj_meta*.csv` | итерация 1 | актуально как отрицательный результат |
| `folds.csv` | фолды E1–E6 | актуально для итерации 1–2 (E7+ строят фолды тем же `GroupKFold` в коде) |
| `smoke_mahmoud/*` (9 файлов) | проверка пайплайна E4 | вспомогательное |
| `g0_final_messages.csv` | G0: детектор по каждой траектории; используется в демо, E7, E10, ручной проверке | актуально |
| `g0_manual_sample.csv` | G0: выборка 38 строк, проверенная ИИ | актуально (исключена из новой выборки) |
| `g0_pilot/*` | пилот G0 | вспомогательное |
| `g0_human_sample.csv` (окончательные метки), `g0_human_key.csv` (ключ), `g0_ai_labels.csv` (предразметка модели), `g0_human_independent.csv` (независимые метки автора, id 1–39), `g0_labels_author_1_39.csv`, `g0_ai_agreement.json`, `g0_human_results.json`, `g0_human_results_author_1_39.json`, `g0_mutual_information.json` | проверка детектора G0 | актуально |
| `e5_features.csv`, `e5_gate.json`, `e5_importance.csv`, `e5_summary.csv`, `e5_transfer.csv` | итерация 2 | устарело по постановке (не перенесено: правило уборки не трогать `outputs/`) |
| `e6_corr.csv`, `e6_external_features.csv`, `e6_features.csv`, `e6_selection.json`, `e6_single_auroc.csv`, `e6_summary.csv`, `e6_utility.csv`, `e6_vif.csv` | итерация 2 | устарело (не перенесено, то же правило) |
| `e7_metrics.csv`, `e7_diffs.csv`, `e7_oof_risk.csv.gz`, `e7_summary.json`, `e7_addendum.csv` | E7 | актуально |
| `e8_metrics.csv`, `e8_runs.csv.gz`, `e8_summary.json` | E8 | актуально |
| `e9_results.csv`, `e9_risks.csv.gz`, `e9_per_task.csv.gz`, `e9_link_e8.csv`, `e9_summary.json` | E9 | актуально |
| `e10_checks.csv.gz`, `e10_features.csv.gz`, `e10_leaderboard.csv`, `e10_kth.csv`, `e10_kth_risks.csv.gz`, `e10_oof_risk.csv.gz`, `e10_selection.csv`, `e10_unknown.csv`, `e10_descriptive.csv`, `e10_summary.json` | E10 | актуально |
| `e11_sample.csv`, `e11_cost.json`, `e11_check.json`, `e11_responses.jsonl`, `e11_parsed.csv`, `e11_attempts.csv`, `e11_metrics.csv`, `e11_groups.csv`, `e11_summary.json`, `e11_manual_sample.csv` | E11 | актуально (`e11_manual_sample.csv` ждёт разметки) |
| `e11b_sample.csv`, `e11b_sample_info.json`, `e11b_cost.json`, `e11b_responses.jsonl`, `e11b_parsed.csv`, `e11b_attempts.csv`, `e11b_metrics.csv`, `e11b_summary.json` | E11b | актуально |
| `e12_metrics.csv`, `e12_diffs.csv`, `e12_oof_risk.csv.gz`, `e12_summary.json` | E12 | актуально |
| `e13_inventory.csv`, `e13_transfer.csv`, `e13_format.csv`, `e13_combos.csv`, `e13_patch_metrics.csv`, `e13_patch_diffs.csv`, `e13_patch_features.csv.gz`, `e13_kth_patch.csv`, `e13_summary.json` | E13 | актуально (код инвентаризации — вне репозитория, `CLEANUP_FINDINGS.md` 3.5) |
| `e14_runs.csv`, `e14_parse.csv`, `e14_metrics.csv`, `e14_combos.csv`, `e14_summary.json` | E14 | актуально |
| `e15_*` | E15 (части 2, 3 — две настройки KTH с 12.11.2026) | актуально |
| `e16_*` | E16 | актуально |
| `kth_deepswe_labels.json`, `e15_label_check.json` | проверка меток DeepSWE-Preview | актуально |
| `kth_with_deepswe/` | прежние выходы E8, E9, E10, E15 по трём настройкам (приложения отчётов) | архив |

Не в git: `outputs/features.csv` (109 МБ; есть `.gz`) и рабочие таблицы `*.xlsx` проверки G0.

## own_data/ (собственный корпус, 771 файл)

| путь | что это | статус |
|---|---|---|
| `PROTOCOL.md` | протокол сбора, версии 1.0–1.2 | актуально |
| `LOG.md` | журнал сбора | актуально |
| `STAGE0_REPORT.md`, `stage0_analysis.py`, `stage0_results.json` | этап 0: конфиги лидерборда, стоимость, пул задач | актуально |
| `STAGE2_REPORT.md` | пилот: 15 прогонов, 7 из 8 критериев | актуально |
| `THINKING_CHECK.md`, `thinking_check.py`, `thinking_check.json` | проверка `thinking: disabled` (основание версии 1.1) | актуально; `thinking_check.py` требует `tiktoken` (`CLEANUP_FINDINGS.md` 3.8) |
| `DIAG_RESOLVE_RATE.md` | диагностика доли решённых | актуально |
| `GOLD_VALIDATION.md`, `gold_validation.py`, `gold_validation.json`, `gold_validation_pre.csv` | валидация окружения эталонными патчами (версия 1.2: 65 из 65) | актуально |
| `label_audit_corpus.csv` | метки аудита `psf/requests` (httpbin.org); отчёт аудита перенесён в `reports/LABEL_AUDIT_REQUESTS.md` (2026-11-13) | актуально |
| `runs_own.parquet`, `steps_own.parquet`, `scores_own.csv` | таблицы прогонов, шагов, рисков | актуально (`in_analysis` у `requests-1766` исправлен 2026-11-10, `LOG.md`) |
| `tasks_pool.json`, `tasks_pilot.json`, `tasks_main.json`, `select_main_tasks.py`, `forecast_main.json` | пул задач, выбор, прогноз стоимости | актуально |
| `eval_summary.json`, `make_eval_summary.py` | сводка `report.json` для наглядного ноутбука | вспомогательное |
| `images_main.log`, `main_console.log`, `process_status.json` | журналы основного сбора | вспомогательное |
| `audit/` (77) | скрипты и результаты аудита меток | актуально (два скрипта с BOM) |
| `config/` (4) | конфиги агента v1.0, v1.1, реестр litellm | актуально |
| `diag/` (60) | диагностика (сравнение с корпусом) | вспомогательное |
| `eval/` (368) | вывод оценки swebench: эталонные патчи (`gold_pre`) и собственные прогоны | актуально; `test_output.txt` ломают голый `pytest` (`CLEANUP_FINDINGS.md` 3.2) |
| `infra/` (29) | скрипты сбора: PowerShell, WSL, Docker, litellm | актуально (отдельное окружение) |
| `leaderboard_configs/` (16) | конфиги прогонов лидерборда | актуально |
| `probes/` (6) | пробные прогоны (без рассуждений) | вспомогательное |
| `raw/` (150) | сырые траектории и журналы собственных прогонов | актуально |
| `raw_failed/` (32) | прерванные попытки | вспомогательное |
