# Выходы повторов KTH с DeepSWE-Preview (три настройки)

Копии выходов E8, E9, E10 и E15 (части 2 и 3) в том виде, в каком они были до исключения DeepSWE-Preview
(2026-11-12; состояние коммита 5b93324). Основной анализ повторов с этой даты идёт по двум настройкам (Qwen3-32B,
devstral-2512); причина — `reports/KTH_DEEPSWE_LABELS.md`. Числа отсюда приводятся в приложениях отчётов E8, E9, E10,
E15.

Воспроизведение тем же кодом: `KTH_WITH_DEEPSWE=1` (переменная окружения) перед `python -m src.e8_repeats run`,
`python -m src.e9_best_of_k`, `python -m src.e10_block_o`, `python -m src.e15_consolidation kth` и `variance`; выходы
пишутся в эту папку. Скопированы только файлы, которые зависят от состава настроек KTH; выходы E10 по лидерборду
(`e10_checks.csv.gz`, `e10_leaderboard.csv`, `e10_unknown.csv`, `e10_oof_risk.csv.gz`) от него не зависят и остаются
в `outputs/`.
