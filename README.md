# Оценка достоверности выполнения задач LLM-агентами по структуре траектории

Репозиторий магистерской диссертации (AITU, Applied AI). Кодовый агент сдаёт патч и тем самым заявляет, что задача
решена. Модель по траектории агента оценивает вероятность того, что сданный результат не проходит скрытые тесты, и
ранжирует результаты для выборочной проверки человеком. Данные: SWE-bench Verified (лидерборд bash-only,
16 связок «агент × модель», 7 864 отправленных прогона) и повторы одной модели ASSERT-KTH. Экспериментальная часть
завершена (G0, E1–E14).

## С чего начать

| файл | что там |
|---|---|
| `REVIEW_PACKET.md` | пакет для рецензента: часть A — постановка, данные, протокол, критерии; часть B — результаты и выводы |
| `docs/EXPERIMENTS.md` | все этапы: вопрос, коммиты протокола и результатов, критерии, ключевые числа, отчёт, скрипт |
| `docs/PROJECT_DOCUMENTATION.md` | полная документация проекта (версия 3) |
| `docs/REPRODUCE.md` | как воспроизвести каждый этап; чего нет в репозитории и почему |
| `docs/LITERATURE.md` | литература |
| `DATASET_CARD.md` | источники данных, ревизии, SHA256 |
| `INVENTORY.md` | что лежит в репозитории и статус каждого файла |
| `CLEANUP_FINDINGS.md` | известные несоответствия и находки |

## Карта каталогов

| каталог | содержимое |
|---|---|
| `reports/` | протоколы (`E*_PROTOCOL.md`, закоммичены до расчётов) и отчёты этапов |
| `src/` | код: адаптеры данных (`src/adapters/`), этапы `e5_…` – `e14_…`, детектор G0, точка входа `src/cli.py` |
| `demo_lib/` | 21 признак структуры `S` и разметка фаз (используются этапами E7–E14) |
| `notebooks/` | демо-ноутбуки (Colab) и сборка демо-данных |
| `data/demo/` | таблица прогонов `runs.parquet` (остальные данные в репозиторий не входят, см. `docs/REPRODUCE.md`) |
| `outputs/` | результаты всех этапов (CSV, JSON) |
| `own_data/` | собственный корпус (glm-4.6, повторы) |
| `config/` | источники данных, шаблоны детекторов |
| `tests/` | модульные тесты: `python -m pytest tests` |
| `docs/` | документация; `docs/checksums/` — SHA256 данных |
| `archive/` | устаревшие материалы прежней постановки (`archive/README.md`) |

## Главный результат: M+S, AUROC 0.685

Итоговая модель `M+S` — связка «агент × модель» + 21 признак структуры. На 7 864 отправленных прогонах:
- общий AUROC **0.685**;
- AUROC внутри задачи 0.689;
- precision@10% 0.656 при доле нерешённых 0.330.

Источник — `reports/E7_REPORT.md`.

Воспроизвести:

1. **Быстро, около 2 минут.** Открыть `notebooks/visual_demo.ipynb` в Colab и выполнить «Runtime → Run all». Ноутбук
   берёт `data/demo/runs.parquet` и проверяет числа через `assert`: `M+S` 0.685 / 0.689, precision@10% 0.656.

   [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Kiruha667/diss/blob/main/notebooks/visual_demo.ipynb)

2. **Полностью.** `pip install -r requirements.txt` (Python 3.11), затем `python -m src.e7_sources data` и
   `python -m src.e7_sources run`. Нужен разобранный корпус `data/interim/swebench_bashonly/trajectories.jsonl.gz`;
   как его получить — в `docs/REPRODUCE.md`. Код останавливается, если структура `S` не воспроизводит 0.662 / 0.640.

Технический демо-ноутбук `notebooks/demo.ipynb` (данные, признаки, обучение `S`):
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Kiruha667/diss/blob/main/notebooks/demo.ipynb)

Прежний README (октябрь 2026) — `archive/README_2026-10.md`.
