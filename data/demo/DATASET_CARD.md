# Карточка демонстрационного датасета

Данные для `notebooks/demo.ipynb`. Собраны скриптом `notebooks/build_demo_data.py` из результатов существующего пайплайна; код пайплайна и `outputs/` не изменялись.

## Источник

- SWE-bench Verified, каркас mini-SWE-agent, режим bash-only: публичный бакет `https://swe-bench-submissions.s3.amazonaws.com`, префикс `bash-only/`; 16 прогонов-сабмитов (папки ниже, дата сабмита — в имени):
  - `20250726_mini-v1.0.0_gemini-2.5-pro`
  - `20250822_mini-v1.9.1_glm-4.5`
  - `20250929_mini-v1.13.3_sonnet-4-5-20250929`
  - `20251118_mini-v1.15.0_gemini-3-pro-preview-20251118`
  - `20251124_mini-v1.17.0_minimax-m2`
  - `20251201_mini-v1.17.1_glm-4.6`
  - `20251209_mini-v1.17.2_devstral-2512`
  - `20251209_mini-v1.17.2_devstral-small-2512`
  - `20260217_mini-v2.0.0_gemini-3-flash-high`
  - `20260226_mini-v2.0.0_gemini-3-pro-high`
  - `20260217_mini-v2.0.0_deepseek-3-2-high`
  - `20260217_mini-v2.0.0_glm-5-high`
  - `20260217_mini-v2.0.0_kimi-k2-5-high`
  - `20260217_mini-v2.0.0_minimax-2-5-high`
  - `20260217_mini-v2.0.0_claude-4-5-sonnet-high`
  - `20260217_mini-v2.0.0_claude-4-5-haiku-high`
- Траектории приведены к единой схеме адаптером `src/adapters/swebench_bashonly.py` (этап E0, 7975 траекторий после исключения 25 с инфраструктурными ошибками или без шагов агента); метка — `report.json` каждого прогона.
- Снапшот: у бакета нет ревизий; используется копия, скачанная для E0 (отчёт E0 закоммичен 2026-09-24), — разобранный набор `data/interim/swebench_bashonly/trajectories.jsonl.gz` от 2026-09-25.

## Файлы

| файл | строк | размер | SHA256 |
|---|---|---|---|
| `runs.parquet` | 7975 | 1.6 МБ | `7b5aa9e487d6f397ec99ab1609557373621e0b639e844556b917d575339d785c` |
| `steps.parquet` | 465630 | 66.8 МБ | `8d3f125e97223b408dd84780355c4ab6e36c6b04d74159f58fe2f61d7a517b90` |
| `examples.json` | 5 | 0.7 КБ | `c8c906f8574d9a86e6ddf190b41ab59a924137d4b818462fbff4d16181efa372` |
| `demo_lib.zip` | — | 5.4 КБ | `b122a22f9d5d864e7530bf7593fcc38b1905501046f1916eb2bc68f583f97004` |

`runs.parquet`, `examples.json` лежат в репозитории (`data/demo/`); все четыре файла — в GitHub Release `demo-data-v1` репозитория `Kiruha667/diss`.

## runs.parquet — одна строка на прогон

| колонка | тип | смысл |
|---|---|---|
| `run_id` | large_string | уникальный id прогона: `<папка прогона в S3>/<instance_id>` |
| `task_id` | large_string | id задачи SWE-bench (instance_id) |
| `repo` | large_string | репозиторий задачи `owner/repo`; выведен из instance_id `owner__repo-N` (в данных отдельного поля нет) |
| `model` | large_string | LLM агента (model_id адаптера E0) |
| `model_family` | large_string | семейство модели (config/datasets.yaml) |
| `agent_version` | large_string | версия mini-SWE-agent (из agent_id) |
| `n_steps` | int64 | число шагов агента (сообщений assistant) |
| `exit_status` | large_string | статус выхода mini-SWE-agent из траектории (`info.exit_status`) |
| `submitted` | int64 | 1, если exit_status == `Submitted` (агент отправил результат) |
| `claim_success` | int64 | текстовое заявление об успехе в финальном сообщении — детектор G0 (`outputs/g0_final_messages.csv`, has_claim) |
| `final_message` | large_string | финальное сообщение агента по правилам G0 (последнее сообщение assistant с ≥ 30 символами естественного языка, не дальше 3 шагов назад), до 1000 символов |
| `resolved` | int64 | 1, если скрытые тесты SWE-bench прошли (`report.json` прогона, поле resolved) |
| `phase_seq` | large_string | фазы шагов через пробел: E / V / O (легенда ниже) |
| `has_any_verification` | double | была ли хоть одна проверка |
| `n_verifications` | double | шагов с проверкой |
| `verif_after_last_edit` | double | есть проверка после последней правки |
| `n_verif_after_last_edit` | double | проверок после последней правки |
| `verified_before_first_edit` | double | проверка до первой правки |
| `n_steps_after_last_edit` | double | шагов после последней правки (равен seg_len_steps) |
| `share_edit` | double | доля шагов-правок |
| `share_verify` | double | доля шагов-проверок |
| `share_other` | double | доля прочих шагов |
| `n_transitions` | double | смен фазы между соседними шагами |
| `phase_bigram_entropy` | double | энтропия биграмм фаз / log2 9 |
| `share_before_first_edit` | double | доля шагов до первой правки (1, если правок нет) |
| `repeat_ratio` | double | доля команд, совпадающих с предыдущей |
| `distinct_cmd_ratio` | double | уникальных команд / шаги |
| `nonzero_rc_share` | double | доля выводов среды с ненулевым кодом возврата |
| `n_files_edited` | double | разных файлов правилось |
| `n_file_switches` | double | переключений между файлами правок |
| `seg_len_steps` | double | шагов после последней правки |
| `seg_len_sent` | double | фрагментов рассуждения после последней правки |
| `n_edits` | double | шагов с правкой |

Признаки — 21 признак модели `M_struct+len` (E5) с именами из `src/e5_claims.py`; посчитаны `process_record` по всей траектории.

## steps.parquet — одна строка на шаг агента

| колонка | тип | смысл |
|---|---|---|
| `run_id` | string | к какому прогону |
| `step_idx` | int32 | номер шага с 0 |
| `thought` | string | текст шага без блоков кода и служебных токенов (рассуждение + видимый текст), до 500 символов |
| `command` | string | выполненные команды шага через перевод строки, до 300 символов |
| `output` | string | вывод среды по командам шага (без префикса кода возврата), до 300 символов |
| `returncode` | int32 | код возврата: первый ненулевой код выводов шага, иначе 0; пусто, если у шага нет вывода |
| `phase` | string | фаза шага: edit / verify / other |
| `commands` | list<element: string> | дополнительно: полный текст выполненных команд (список) — для точного пересчёта фаз в `score_trajectory` |
| `n_outputs` | int16 | дополнительно: число выводов среды у шага |
| `n_nonzero_rc` | int16 | дополнительно: сколько из них с ненулевым кодом возврата |
| `n_sent` | int32 | дополнительно: число фрагментов reason_sent (сегментация E1) в полном тексте шага — нужно для `seg_len_sent`, полный текст рассуждения в датасет не входит |

## Легенда фаз

Разметка существующего кода (`src/g0_false_success.classify_segment`, `src/e5_claims.command_events`) над выполненными bash-командами шага; если в шаге и правка, и проверка — фаза edit.

| код | фаза | правило |
|---|---|---|
| E | edit | sed -i / perl -i, запись в файл кода через > / >> / tee / heredoc, git apply, patch, apply_patch / str_replace / edit, python-код, пишущий файлы |
| V | verify | pytest / py.test / tox / nosetests, python -m pytest / unittest / nose, make test, run_tests / runtests, bin/test, python -c или heredoc с assert, запуск файла с assert |
| O | other | всё остальное (чтение, поиск, запуск без проверки, служебные команды) |

## Правило submitted

`submitted = 1`, если статус выхода траектории `Submitted`; остальные статусы в данных: `LimitsExceeded` (108), `RetryError` (3).

## Известные ограничения

- `claim_success` — регулярные выражения G0 (`config/claim_patterns.txt`); заявления в других формулировках не находятся.
- Форматы логов различаются: v1.x — текст с блоком ```bash в ответе и кодом возврата в `<returncode>`; v2.x — вызовы инструментов и сообщения role=tool. Рассуждения моделей лежат в разных полях (reasoning_content / reasoning / reasoning_details / extra).
- Адаптер хранит только ненулевые коды возврата; нулевой код в `returncode` выведен (в локально доступных сырых логах код возврата есть у каждого вывода — проверка E6, блок 1).
- `final_message` восстановлено из разобранных траекторий теми же функциями G0 (сырые логи локально не все): расхождений с G0 — заявление 0, номер шага 0, длина 2 из 7975.
- `thought`, `command`, `output` усечены; полные команды — в `commands`.

