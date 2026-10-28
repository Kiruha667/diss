# Этап V — валидация окружения эталонными патчами (gold_pre)

Протокол 1.2, раздел 11.7. Дата: 2026-10-28. Вызовов LLM API нет.

## Что сделано

- Эталонные патчи SWE-bench Verified (`--predictions_path gold`) всех 65 задач `tasks_main.json` оценены нашей
  оценкой: swebench 4.1.0, `--dataset_name princeton-nlp/SWE-bench_Verified --split test --max_workers 1
  --namespace swebench --instance_image_tag latest --cache_level instance --clean False --timeout 1800`,
  `run_id gold_pre` — те же флаги и те же образы, что у оценки прогонов агента (`infra/gold_eval.sh`).
- Образы всех 65 задач загружены заранее (`infra/pull_main_images.sh`, `images_main.log`: 61 загружено, 4 уже были,
  0 ошибок); во время оценки ничего не загружалось.
- Результаты: `eval/gold_pre/<задача>/` (`report.json`, `test_output.txt`, `run_instance.log`),
  `eval/gold_pre/eval_meta.json`, сводный отчёт harness `eval/gold_pre/gold.gold_pre.json`; по задачам —
  `gold_validation_pre.csv`; решение об исключении — `gold_validation.json` (`gold_validation.py pre`).
- Время: 2026-10-28 11:44:42 – 13:00:23 UTC (1 ч 16 мин) на ноутбуке; WSL ограничен `.wslconfig` (5 ГБ памяти,
  3 потока). По `run_instance.log`: медиана 49 с на задачу, минимум 31 с, максимум 515 с (django__django-10097),
  далее matplotlib-24177 280 с, matplotlib-25960 252 с.

## Результат

| | число |
|---|---|
| задач | 65 |
| `report.json` получен | 65 |
| патч применился | 65 |
| `resolved = true` | **65** |
| упавших тестов FAIL_TO_PASS / PASS_TO_PASS | 0 / 0 (прошло 562 / 7914) |
| пустых патчей / ошибок оценки (harness) | 0 / 0 |
| **исключено из пула** | **0** |

По репозиториям (все resolved): django 34, sympy 13, astropy 3, pytest-dev 3, scikit-learn 3, sphinx-doc 3,
matplotlib 2, pydata 2, pylint-dev 2.

Задач, где эталон не прошёл, нет, поэтому разбор упавших тестов и внешних зависимостей (сеть, сертификат, пакет,
образ) не требуется. Правило остановки (исключено больше 5 задач) не сработало: пул основного сбора — все 65 задач,
основной сбор начинается (11.8).

## Замечания

- psf/requests в пуле нет (исключены в 1.2), поэтому найденные при аудите меток изменения httpbin.org
  (`LABEL_AUDIT_REQUESTS.md`) на этот пул не влияют; gold_pre это подтверждает на уровне тестов из
  FAIL_TO_PASS / PASS_TO_PASS.
- gold_pre проверяет только тесты, по которым считается метка; тесты вне этих списков не проверялись.
- Валидация — снимок на 2026-10-28. Внешняя среда (сеть, сторонние сервисы) может измениться за время сбора;
  для этого после сбора — повторная оценка `gold_post` и таблица расхождений (11.7).
