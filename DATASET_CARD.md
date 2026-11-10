# DATASET_CARD — источники данных, ревизии, снапшоты, SHA256

Карточка всех источников данных проекта. Хеши посчитаны 2026-11-10 по локальным файлам ноутбука автора.
- Пофайловые списки лежат в `docs/checksums/<набор>.sha256` (формат `sha256sum`, пути от корня репозитория).
- «Хеш набора» — это SHA256 самого файла-списка. Совпадение хеша набора означает совпадение всех файлов и их путей.
- Сырые данные в репозиторий не входят (`.gitignore`: `data/*`), кроме `data/demo/runs.parquet` и собственного корпуса
  `own_data/`. Карточка демо-данных — `data/demo/DATASET_CARD.md`.

Проверить локальную копию: `sha256sum -c docs/checksums/<набор>.sha256` из корня репозитория (Linux / Git Bash).

## 1. Корпус лидерборда SWE-bench Verified, bash-only (основной)

- **Источник.** Публичный бакет `s3://swe-bench-submissions/bash-only/<прогон>/` (неподписанный HTTPS):
  - траектории — `trajs/<задача>/<задача>.traj.json`;
  - метки — `logs/<задача>/report.json`, поле `resolved`, получено скрытыми тестами бенчмарка.
- **Метаданные прогонов** — `metadata.yaml` из `github.com/SWE-bench/experiments`, ветка `main`; коммит не закреплён.
- **Подмножество D2** — 16 прогонов (`config/datasets.yaml`), 500 задач, 7 975 траекторий.
- **Снапшот.** Скачивание и разбор шли на Kaggle 2026-09-23/24 (этап E0, ядро `diss-e0`). Версионирования у бакета нет:
  дата снапшота — дата скачивания.
- **Лицензия** источника в проекте не проверялась.

| набор | где | файлов | объём | хеш набора / файла | дата файлов |
|---|---|---|---|---|---|
| сырые траектории, полный набор (≈ 7.5 ГиБ) | Kaggle, `data/raw/swebench_bashonly/` | — | — | **нет локально**: локально 355 траекторий | 2026-09-23/24 |
| сырые траектории, локальная часть: 355 `traj.json` + 355 `report.json` + 16 `metadata.yaml` + 16 `manifest.csv` | `data/raw/swebench_bashonly/` | 742 | 198.2 МБ | `07ff628c7de78bcf192031019f7322c9a4bfcc4a6ccc463ab2c52b614e9408d6` | 2026-09-24 … 25 |
| разобранный корпус, схема E0, 7 975 траекторий | `data/interim/swebench_bashonly/trajectories.jsonl.gz` | 1 | 180.4 МБ | `af64c8b294448d356b902ac5b78294444869ad1233f4a3e2a10dd1d6aa12442c` | 2026-09-25 |
| исключённые на E0 | `data/interim/swebench_bashonly/excluded.csv` | 1 | | `3d752bd1ad264119508d4c61f805c609b9466a8016d72939a907c911297ecf4b` | 2026-09-24 |
| таблица прогонов (в репозитории) | `data/demo/runs.parquet` | 1 | | `7b5aa9e487d6f397ec99ab1609557373621e0b639e844556b917d575339d785c` (совпадает с `data/demo/SHA256SUMS`) | — |
| полные финальные сообщения и рассуждения (E7) | `data/interim/swebench_bashonly/e7_texts.parquet` | 1 | 92.0 МБ | `e8e8c03c846ad82ca9c232bde39e6578b3e457f9e38952b366c34230ff8d63e9` | 2026-11-03 |
| `logs/<задача>/patch.diff` — патч модели, не эталон: 7 864 прогона корпуса + 2 000 новых прогонов (E13, E14) | `data/raw/swebench_bashonly_patches/` | 9 864 | 105.1 МБ | `3a9309628b5ace27170cb427cbe525d9b34869d1d929abb0cc3ebd8bb409e5a0` | 2026-11-08 |

`data/demo/steps.parquet` и `demo_lib.zip` опубликованы в GitHub Release `demo-data-v1`; их SHA256 — в
`data/demo/SHA256SUMS`.

## 2. Описания задач SWE-bench Verified (E7, E12)

| что | значение |
|---|---|
| источник | Hugging Face `princeton-nlp/SWE-bench_Verified`, ревизия `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`, файл `data/test-00000-of-00001.parquet` (SHA256 `a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd`) |
| читаются только | `instance_id`, `repo`, `problem_statement`; эталонный патч и тесты не читаются |
| локально | `data/raw/swebench_verified/`: 5 файлов, 2.5 МБ, хеш набора `e0e129180459aaba64169471ab4b2feca6bf0e6b6bbd1d57613ec59b31b23f9b` |
| дата | 2026-11-03 |

## 3. Повторы ASSERT-KTH (E8–E11, E11b, E13)

| что | значение |
|---|---|
| источник | Hugging Face `ASSERT-KTH/agentic-evals-artifacts`, ревизия `5db0c4b69382d160a313d7ceaded915398c63e13`; лицензия CC BY 4.0 (по E8_REPORT); статья arXiv 2602.07150 |
| использовано | три настройки nano-agent при t = 0.6: Qwen3-32B, DeepSWE-Preview, devstral-2512; по 10 прогонов на задачу |
| локально | `data/raw/agentic_evals/`: 185 файлов, 2.02 ГБ, хеш набора `69a976c71be18090ac58a7204c026a3a0c34d15284ecd1fda8bf89167e20e28c` |
| разобрано | `data/interim/agentic_evals/runs.parquet` `4d6638d1bbee888168399b0f0694b1ace8f3143a3228ead995cb2ac2785d857f`; `steps.parquet` `2567b782371541fb5018394b02d0d694880fa277457217ec4418f04650876709`; `build_info.json` `4c136f6de0ba8b87d4492d5bda0c02186f4cb315b564a0cac25ffaa5b1a5b781` |
| дата | 2026-11-03 |

## 4. Exgentic (внешняя валидация E6, сейчас в `archive/`)

| что | значение |
|---|---|
| источник | Hugging Face `Exgentic/agent-llm-traces-v2`, ревизия `4b8ad4ab198438e5a170f9171c19c6a2cf7c1814`; лицензия в проекте не проверялась |
| локально | `data/raw/exgentic/`: 23 файла, 231.7 МБ, хеш набора `75147fee2a6af095abdfc10997604fd8c6fe8a093a664293f48dc40dbf3df120` |
| разобрано | `data/interim/exgentic/trajectories.jsonl.gz` `309c7256ba7f3480d2d79656953cabd9752e1901a3d1197dc2d1d0eb2efc32f4`; `excluded.csv` `6b911b02f5cd977af7e0a438b811aa0c9c6ec7c6ef37219b5cb544d6d41845e2` |
| дата | 2026-10-07 |

## 5. Новые прогоны лидерборда, февраль 2026 (E13 шаг 0, E14)

| что | значение |
|---|---|
| источник | тот же бакет `s3://swe-bench-submissions/bash-only/`; прогоны `20260217_mini-v2.0.0_claude-4-5-opus-high`, `20260217_mini-v2.0.0_claude-4-6-opus`, `20260217_mini-v2.0.0_gpt-5-2-high`, `20260219_mini-v2.0.0_gpt-5-2-codex` |
| локально | `data/raw/swebench_bashonly_new/`: 4 008 файлов (2 000 траекторий, 2 000 `report.json`, `metadata.yaml`, `manifest.csv`), 637.4 МБ, хеш набора `472e68c2340ffca25745826022f58d623123139f7331d30237c14f00a3731363` |
| `report.json` 9 прогонов-кандидатов (инвентаризация E13) | `data/raw/swebench_bashonly_inventory/`: 3 498 файлов, 34.6 МБ, хеш набора `09fc8273d5ab9626cfe445a413d1417a419719c1be17954a6f9d07695fa7d9cf` |
| дата | 2026-11-08 |
| замечания | GPT-5.2 записан в формате OpenAI Responses API — адаптер E0 его не разбирает; траектории `gpt-5-2-codex` побайтно совпадают с `gpt-5-2-high` (`CLEANUP_FINDINGS.md`) |

## 6. Собственный корпус (`own_data/`, в репозитории)

| что | значение |
|---|---|
| что | glm-4.6 (z.ai), mini-SWE-agent 1.17.1, оценка swebench 4.1.0 в Docker / WSL2; протокол `own_data/PROTOCOL.md` |
| таблицы | `own_data/runs_own.parquet` `9d0c27cf92b8786e658396dabd6d2f35b72ce94bb9e76fea0f9a17bd2c3da90e` (после исправления `in_analysis` 2026-11-10; было `4edb2d28…`, `own_data/LOG.md`); `own_data/steps_own.parquet` `f1e22e746380d1d46fb854bd1d5117d1e1a9a5623aeb4eb585f17bf1acecacd6` |
| аудит меток `psf/requests` (логи оценки корпуса) | `data/raw/label_audit/`: 592 файла, 29.9 МБ, хеш набора `8af9332673c04936e065d664abc9e5d6385c0bd0305868eb63f2b6ffad23d898` (2026-10-28) |
| дата сбора | 2026-10-26 … 28 (`own_data/LOG.md`) |

## 7. Прочее

| что | значение |
|---|---|
| Mahmoud-queens/swe-agent-subset-selection-vectors (только проверка пайплайна E4) | `data/raw/mahmoud_vectors/`: 3 004 файла, 803.5 МБ, хеш набора `65e6105fde0b8f4096dc2c02e040a45d298a4ef12b69b18add7b98ce1c95e76f`; ревизия в коде не закреплена (`hf_hub_download` без `revision`) |
| токенизатор и шаблон чата Devstral 2 (прогноз стоимости E11) | Hugging Face `mistralai/Devstral-2-123B-Instruct-2512`, ревизия не записана; `data/raw/devstral2_tokenizer/`: 11 файлов, 17.1 МБ, хеш набора `53d168786932d10f58df47e8a65cfa7ea630b1613207a323e9b41903281139d5` (2026-11-05) |
| энкодер all-MiniLM-L6-v2 (E1, E12) | `sentence-transformers/all-MiniLM-L6-v2`, ревизия `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` (записана в `outputs/e12_summary.json`); веса в кэше Hugging Face, в `data/` **нет локально** |
| эмбеддинги E1 | полный набор считался на Kaggle (ядро `diss-e1`); локально в `data/emb*/` только подвыборка — **полного набора нет локально** |
