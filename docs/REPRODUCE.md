# Воспроизведение

Окружение:
- Python 3.11 (проверено на 3.11.16), версии всех пакетов закреплены в `requirements.txt` (86 пакетов, все через `==`);
- установка: `uv pip install -r requirements.txt --index-strategy unsafe-best-match` или
  `pip install -r requirements.txt`;
- CPU достаточно, GPU нужен только для эмбеддингов E1 (их считали на Kaggle);
- все команды выполняются из корня репозитория;
- источники, ревизии и SHA256 данных — в `DATASET_CARD.md`.

**Время** указано по `outputs/*_summary.json` или по журналам прогонов. **Память** почти нигде не измерялась — это
отмечено в таблице.

## 0. Главный результат за 3 минуты (M+S, AUROC 0.685)

1. Открыть `notebooks/visual_demo.ipynb` в Colab (ссылка в `README.md`) и выполнить «Runtime → Run all», около 1–2 минут.
   Ноутбук скачивает `data/demo/runs.parquet` из репозитория и сверяет числа через `assert`: `M` 0.593 / 0.742, `M+S`
   0.685 / 0.689 (общий / внутри задачи), precision@10% `M+S` 0.656, 1 987 пар близких по силе моделей.
2. Полный путь E7 — `python -m src.e7_sources data`, затем `python -m src.e7_sources run`. Нужен
   `data/interim/swebench_bashonly/trajectories.jsonl.gz` (180 МБ; в репозитории нет, раздел 2): из него берутся тексты
   для блоков A и R. При запуске код останавливается, если `S` не даёт 0.662 / 0.640.

## 1. Команды по этапам

| этап | команда | входные данные | откуда | время | память | API-ключи |
|---|---|---|---|---|---|---|
| E0 (корпус) | `python -m src.cli download --workers 32`, затем `python -m src.cli inventory`; или на Kaggle: `python -m src.kaggle_job push --stage e0 --user <kaggle_user>` → `status` → `pull` | бакет bash-only, 16 прогонов D2, ≈ 7.5 ГиБ | `s3://swe-bench-submissions` (публичный) | часы; разбирали на Kaggle | не измерялась | нет; для Kaggle — учётные данные Kaggle CLI |
| E1–E4 (итерация 1, хаотичность) | `python -m src.cli segment`, `build --seg …` (GPU, Kaggle: `kaggle_job --stage e1`), `features` (Kaggle `e3`), `stats --all`, `encoder-check`, `smoke-mahmoud` | `trajectories.jsonl.gz`, эмбеддинги | E0; HF `Mahmoud-queens/...` для smoke | часы, GPU | не измерялась | нет |
| G0 (самоотчёт) | `python -m src.cli g0 --download`; на Kaggle `python -m src.g0_false_success push\|status\|pull --user <u>` | сырые `traj.json`, все 7 975 | бакет | на Kaggle | не измерялась | нет |
| E5 (старая постановка, признаки `S`) | `python -m src.cli e5` | `trajectories.jsonl.gz`, сегменты, `outputs/g0_final_messages.csv` | E0, G0 | — | — | нет |
| E6 (в `archive/`) | `python -m src.cli e6 --block 1` … `--block 4` | как E5 + Exgentic | HF `Exgentic/agent-llm-traces-v2@4b8ad4a` | — | — | нет |
| демо-данные | `python notebooks/build_demo_data.py` | `trajectories.jsonl.gz`, `outputs/g0_final_messages.csv` | E0, G0 | — | — | нет |
| демо-ноутбуки | `notebooks/demo.ipynb`, `notebooks/visual_demo.ipynb` (Colab) | `data/demo/runs.parquet` (в репозитории), `steps.parquet` и `demo_lib.zip` из GitHub Release `demo-data-v1` | репозиторий | ≈ 3 мин / 1–2 мин | Colab | нет |
| E7 | `python -m src.e7_sources data`, затем `run`; дополнение `python -m src.e7_addendum` | `runs.parquet`, `trajectories.jsonl.gz`, SWE-bench Verified (`instance_id`, `repo`, `problem_statement`) | HF `princeton-nlp/SWE-bench_Verified@c104f84` | — (блок R — TF-IDF всех рассуждений, самый тяжёлый) | не измерялась | нет |
| E8 | `python -m src.e8_repeats check`, `build`, `run` (с 12.11.2026 анализ — две настройки; прежний расчёт с DeepSWE-Preview — `KTH_WITH_DEEPSWE=1`, выходы в `outputs/kth_with_deepswe/`; доказательства по меткам — `python -m src.kth_deepswe_labels`) | повторы ASSERT-KTH, 1.9 ГБ | HF `ASSERT-KTH/agentic-evals-artifacts@5db0c4b` | `build` ≈ 7 мин (`build_info.json`: 424 с), `run` ≈ 6 с | не измерялась | нет |
| E9 | `python -m src.e9_best_of_k` | данные E8 | — | ≈ 43 с | — | нет |
| E10 | `python -m src.e10_check_outcomes checks`, `parser-check`; затем `python -m src.e10_block_o` | `trajectories.jsonl.gz`, данные E8 | — | `e10_block_o` ≈ 3.6 мин; разбор — дольше | `data/interim/e10/commands.parquet` ≈ 157 МБ | нет |
| E11 | `python -m src.e11_elicitation sample`, `cost`, `check`, `ask`, `analyze` | KTH devstral-2512 (данные E8); токенизатор Devstral 2 только для `cost` | OpenRouter → Mistral | `ask` ≈ 50 мин, 800 запросов (журнал `data/interim/e11/ask2.log`) | — | **да**: `OPENROUTER_API_KEY`; расход $3.37 |
| E11b | `python -m src.e11b_finished_pairs sample`, `cost`, `ask`, `analyze` | как E11 | OpenRouter → Mistral | ≈ 17 мин, 244 запроса | — | **да**: `OPENROUTER_API_KEY`; расход $1.46 |
| E12 | `python -m src.e12_task_difficulty` | как E7 + all-MiniLM-L6-v2 | HF | ≈ 4 мин | — | нет |
| E13 | `python -m src.e13_generations_patch patches` (скачивает 7 864 `patch.diff`), затем `run` | как E7; `logs/<task>/patch.diff`; данные E8 | бакет | `run` ≈ 1 мин | — | нет |
| E14 | `python -m src.e14_new_models` | `data/raw/swebench_bashonly_new/`: 4 прогона февраля 2026 (ниже) | бакет | ≈ 2.5 мин | — | нет |
| популяция статьи (пересчёт G0, E7, E12, E13, E14 на 7 732 прогонах без пустых патчей) | `python -m src.paper_population run` (части `g0 e7 e12 e13 e14`), затем `report` — раздел «Популяция статьи» в отчётах этих этапов | как E7, E12, E13, E14; `outputs/g0_final_messages.csv` | — | ≈ 18 мин (E7 — 16 мин) | — | нет |
| G0, ручная проверка | `python -m src.g0_human_check sample` (сделано), `analyze` (после разметки автора) | `runs.parquet`, `e7_texts.parquet`, `outputs/g0_final_messages.csv` | — | секунды | — | нет |
| собственный корпус | `own_data/infra/` (PowerShell + WSL2 + Docker), протокол `own_data/PROTOCOL.md` | API z.ai (glm-4.6), образы SWE-bench | z.ai, Docker Hub | не сведено; по прогонам — `own_data/LOG.md` | Docker в WSL2 | **да**: `ZAI_API_KEY`; расход $4.88 |

**Скачать четыре новых прогона для E14.** Отдельного скрипта в репозитории нет: скачивание шло вспомогательным
скриптом (`CLEANUP_FINDINGS.md`). Эквивалент через адаптер:

```python
from src.adapters import swebench_bashonly as sb
from src.common import RAW
cfg = dict(sb.load_cfg())
cfg["runs"] = [{"sub": s, "family": f} for s, f in [
    ("20260217_mini-v2.0.0_claude-4-5-opus-high", "anthropic"), ("20260217_mini-v2.0.0_claude-4-6-opus", "anthropic"),
    ("20260217_mini-v2.0.0_gpt-5-2-high", "openai"), ("20260219_mini-v2.0.0_gpt-5-2-codex", "openai")]]
sb.download(cfg, RAW / "swebench_bashonly_new", workers=8)
```

**Тесты.** `python -m pytest tests` — 36 тестов, около 11 с; покрывают E1–E6, G0 и адаптер Exgentic, но не E7–E14.
Голый `pytest` из корня падает (`CLEANUP_FINDINGS.md`).

## 2. Чего нет в репозитории и почему

| что | объём | почему нет | где взять |
|---|---|---|---|
| сырые траектории лидерборда (`traj.json`, `report.json`) | ≈ 7.5 ГиБ | размер; источник публичный | бакет, команда E0 |
| разобранный корпус `trajectories.jsonl.gz`, `e7_texts.parquet`, сегменты | 180 + 92 МБ | размер; воспроизводится из сырых данных | E0 / E7 `data` |
| эмбеддинги E1 | гигабайты | размер; считались на Kaggle (GPU) | `python -m src.cli build` |
| повторы ASSERT-KTH | 2.0 ГБ | размер; источник публичный (CC BY 4.0) | HF, ревизия в `DATASET_CARD.md` |
| Exgentic | 232 МБ | размер; лицензия в проекте не проверялась | HF, ревизия `4b8ad4a` |
| новые прогоны февраля 2026 | 637 МБ | размер | бакет, раздел 1 |
| `patch.diff` всех прогонов | 105 МБ | воспроизводится | `python -m src.e13_generations_patch patches` |
| сырые логи аудита меток `psf/requests` | 30 МБ | размер | `own_data/audit/` (скрипты) |
| токенизатор Devstral 2 | 17 МБ | сторонние файлы | HF `mistralai/Devstral-2-123B-Instruct-2512` |
| `outputs/features.csv` | 109 МБ | больше лимита GitHub 100 МБ | в репозитории `outputs/features.csv.gz` |
| `data/demo/steps.parquet`, `demo_lib.zip` | — | вынесены в GitHub Release | Release `demo-data-v1`, SHA256 в `data/demo/SHA256SUMS` |
| `outputs/g0_human_key.csv` | 7 КБ | ключ слепой разметки: не публикуется до возврата разметки автором | восстанавливается `python -m src.g0_human_check sample` (seed 47) — только если файла выборки ещё нет |
| API-ключи (`OPENROUTER_API_KEY`, `ZAI_API_KEY`) | — | секреты; только в переменных окружения | у автора |
| PDF и `.docx` автора (ИПРМ, презентации) | — | личные документы | у автора |
