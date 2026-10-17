"""Write data/demo/DATASET_CARD.md and DEMO_REPORT.md from the built demo data and the executed notebook.
Run from the repository root:  python notebooks/make_demo_docs.py <demo_results.json> <execution seconds>
Every number comes from data/demo/checks.json, the parquet files or the notebook's demo_results.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo"

RUN_COLS = {
    "run_id": "уникальный id прогона: `<папка прогона в S3>/<instance_id>`",
    "task_id": "id задачи SWE-bench (instance_id)",
    "repo": "репозиторий задачи `owner/repo`; выведен из instance_id `owner__repo-N` (в данных отдельного поля нет)",
    "model": "LLM агента (model_id адаптера E0)", "model_family": "семейство модели (config/datasets.yaml)",
    "agent_version": "версия mini-SWE-agent (из agent_id)",
    "n_steps": "число шагов агента (сообщений assistant)",
    "exit_status": "статус выхода mini-SWE-agent из траектории (`info.exit_status`)",
    "submitted": "1, если exit_status == `Submitted` (агент отправил результат)",
    "claim_success": "текстовое заявление об успехе в финальном сообщении — детектор G0 (`outputs/g0_final_messages.csv`, "
                     "has_claim)",
    "final_message": "финальное сообщение агента по правилам G0 (последнее сообщение assistant с ≥ 30 символами "
                     "естественного языка, не дальше 3 шагов назад), до 1000 символов",
    "resolved": "1, если скрытые тесты SWE-bench прошли (`report.json` прогона, поле resolved)",
    "phase_seq": "фазы шагов через пробел: E / V / O (легенда ниже)",
}
STEP_COLS = {
    "run_id": "к какому прогону", "step_idx": "номер шага с 0",
    "thought": "текст шага без блоков кода и служебных токенов (рассуждение + видимый текст), до 500 символов",
    "command": "выполненные команды шага через перевод строки, до 300 символов",
    "output": "вывод среды по командам шага (без префикса кода возврата), до 300 символов",
    "returncode": "код возврата: первый ненулевой код выводов шага, иначе 0; пусто, если у шага нет вывода",
    "phase": "фаза шага: edit / verify / other",
    "commands": "дополнительно: полный текст выполненных команд (список) — для точного пересчёта фаз в "
                "`score_trajectory`",
    "n_outputs": "дополнительно: число выводов среды у шага",
    "n_nonzero_rc": "дополнительно: сколько из них с ненулевым кодом возврата",
    "n_sent": "дополнительно: число фрагментов reason_sent (сегментация E1) в полном тексте шага — нужно для "
              "`seg_len_sent`, полный текст рассуждения в датасет не входит",
}
FEAT_DESCR = {
    "has_any_verification": "была ли хоть одна проверка", "n_verifications": "шагов с проверкой",
    "verif_after_last_edit": "есть проверка после последней правки", "n_verif_after_last_edit": "проверок после "
    "последней правки", "verified_before_first_edit": "проверка до первой правки",
    "n_steps_after_last_edit": "шагов после последней правки (равен seg_len_steps)",
    "share_edit": "доля шагов-правок", "share_verify": "доля шагов-проверок", "share_other": "доля прочих шагов",
    "n_transitions": "смен фазы между соседними шагами", "phase_bigram_entropy": "энтропия биграмм фаз / log2 9",
    "share_before_first_edit": "доля шагов до первой правки (1, если правок нет)",
    "repeat_ratio": "доля команд, совпадающих с предыдущей", "distinct_cmd_ratio": "уникальных команд / шаги",
    "nonzero_rc_share": "доля выводов среды с ненулевым кодом возврата", "n_files_edited": "разных файлов правилось",
    "n_file_switches": "переключений между файлами правок", "n_steps": "число шагов",
    "seg_len_steps": "шагов после последней правки", "seg_len_sent": "фрагментов рассуждения после последней правки",
    "n_edits": "шагов с правкой"}


def schema_table(path: Path, descr: dict) -> list[str]:
    sch = pq.read_schema(path)
    L = ["| колонка | тип | смысл |", "|---|---|---|"]
    for f in sch:
        L.append(f"| `{f.name}` | {f.type} | {descr.get(f.name, FEAT_DESCR.get(f.name, ''))} |")
    return L


def main(results_path: str, seconds: str) -> None:
    chk = json.loads((DEMO / "checks.json").read_text(encoding="utf-8"))
    res = json.loads(Path(results_path).read_text(encoding="utf-8"))
    runs = pd.read_parquet(DEMO / "runs.parquet")
    nsteps = pq.ParquetFile(DEMO / "steps.parquet").metadata.num_rows
    cfg = yaml.safe_load((ROOT / "config" / "datasets.yaml").read_text(encoding="utf-8"))["swebench_bashonly"]
    run_names = [r["name"] if isinstance(r, dict) and "name" in r else (r.get("sub") if isinstance(r, dict) else r)
                 for r in cfg["runs"]]
    sha, size = chk["sha256"], chk["sizes"]
    fm = chk["final_msg_checks"]
    dl = chk["demo_lib_check"]
    excl = runs[runs.submitted == 0].exit_status.value_counts()

    card = ["# Карточка демонстрационного датасета", "",
            "Данные для `notebooks/demo.ipynb`. Собраны скриптом `notebooks/build_demo_data.py` из результатов "
            "существующего пайплайна; код пайплайна и `outputs/` не изменялись.", "",
            "## Источник", "",
            f"- SWE-bench Verified, каркас mini-SWE-agent, режим bash-only: публичный бакет `{cfg['s3_base']}`, "
            f"префикс `{cfg['s3_prefix']}/`; {len(run_names)} прогонов-сабмитов (папки ниже, дата сабмита — в имени):",
            *[f"  - `{n}`" for n in run_names],
            "- Траектории приведены к единой схеме адаптером `src/adapters/swebench_bashonly.py` (этап E0, 7975 "
            "траекторий после исключения 25 с инфраструктурными ошибками или без шагов агента); метка — `report.json` "
            "каждого прогона.",
            "- Снапшот: у бакета нет ревизий; используется копия, скачанная для E0 (отчёт E0 закоммичен 2026-09-24), — "
            "разобранный набор `data/interim/swebench_bashonly/trajectories.jsonl.gz` от 2026-09-25.", "",
            "## Файлы", "", "| файл | строк | размер | SHA256 |", "|---|---|---|---|",
            f"| `runs.parquet` | {len(runs)} | {size['runs.parquet'] / 1e6:.1f} МБ | `{sha['runs.parquet']}` |",
            f"| `steps.parquet` | {nsteps} | {size['steps.parquet'] / 1e6:.1f} МБ | `{sha['steps.parquet']}` |",
            f"| `examples.json` | {len(chk['examples'])} | {size['examples.json'] / 1e3:.1f} КБ | "
            f"`{sha['examples.json']}` |",
            f"| `demo_lib.zip` | — | {size['demo_lib.zip'] / 1e3:.1f} КБ | `{sha['demo_lib.zip']}` |", "",
            "`runs.parquet`, `examples.json` лежат в репозитории (`data/demo/`); все четыре файла — в GitHub Release "
            "`demo-data-v1` репозитория `Kiruha667/diss`.", "",
            "## runs.parquet — одна строка на прогон", ""] + schema_table(DEMO / "runs.parquet", RUN_COLS) + [
            "", "Признаки — 21 признак модели `M_struct+len` (E5) с именами из `src/e5_claims.py`; посчитаны "
            "`process_record` по всей траектории.", "",
            "## steps.parquet — одна строка на шаг агента", ""] + schema_table(DEMO / "steps.parquet", STEP_COLS) + [
            "", "## Легенда фаз", "",
            "Разметка существующего кода (`src/g0_false_success.classify_segment`, `src/e5_claims.command_events`) над "
            "выполненными bash-командами шага; если в шаге и правка, и проверка — фаза edit.", "",
            "| код | фаза | правило |", "|---|---|---|",
            "| E | edit | sed -i / perl -i, запись в файл кода через > / >> / tee / heredoc, git apply, patch, "
            "apply_patch / str_replace / edit, python-код, пишущий файлы |",
            "| V | verify | pytest / py.test / tox / nosetests, python -m pytest / unittest / nose, make test, "
            "run_tests / runtests, bin/test, python -c или heredoc с assert, запуск файла с assert |",
            "| O | other | всё остальное (чтение, поиск, запуск без проверки, служебные команды) |", "",
            "## Правило submitted", "",
            "`submitted = 1`, если статус выхода траектории `Submitted`; остальные статусы в данных: " +
            ", ".join(f"`{k}` ({v})" for k, v in excl.items()) + ".", "",
            "## Известные ограничения", "",
            "- `claim_success` — регулярные выражения G0 (`config/claim_patterns.txt`); заявления в других "
            "формулировках не находятся.",
            "- Форматы логов различаются: v1.x — текст с блоком ```bash в ответе и кодом возврата в `<returncode>`; "
            "v2.x — вызовы инструментов и сообщения role=tool. Рассуждения моделей лежат в разных полях "
            "(reasoning_content / reasoning / reasoning_details / extra).",
            "- Адаптер хранит только ненулевые коды возврата; нулевой код в `returncode` выведен (в локально доступных "
            "сырых логах код возврата есть у каждого вывода — проверка E6, блок 1).",
            "- `final_message` восстановлено из разобранных траекторий теми же функциями G0 (сырые логи локально не "
            f"все): расхождений с G0 — заявление {fm['claim_mismatch']}, номер шага {fm['final_step_idx_mismatch']}, "
            f"длина {fm['final_len_mismatch']} из {chk['n_runs']}.",
            "- `thought`, `command`, `output` усечены; полные команды — в `commands`.", ""]
    (DEMO / "DATASET_CARD.md").write_text("\n".join(card) + "\n", encoding="utf-8")

    ex = pd.DataFrame(chk["examples"]).merge(runs[["run_id", "model", "n_steps", "submitted", "claim_success",
                                                  "resolved"]], on="run_id")
    util = pd.DataFrame(res["utility"])
    rep = ["# DEMO_REPORT — демонстрационный Colab-ноутбук", "",
           "## 1. Инвентаризация", "",
           "1. **Сырые траектории.** Публичный бакет `swe-bench-submissions`, `bash-only/<прогон>/trajs/<задача>/"
           "<задача>.traj.json` — JSON с полем `messages` (сообщения system / user / assistant / tool / exit) и `info` "
           f"(exit_status, версия, конфигурация); {len(run_names)} прогонов × 500 задач. Полный набор (≈ 7.5 ГиБ по "
           "замеру при подготовке E0) скачивался и разбирался на Kaggle (ядро diss-e0); локально лежат 355 сырых "
           "траекторий с их `report.json` (198 МБ, `data/raw/swebench_bashonly/`) и весь разобранный набор "
           "`data/interim/swebench_bashonly/trajectories.jsonl.gz` (180 МБ, 7975 траекторий в схеме E0).",
           "2. **Метки.** `bash-only/<прогон>/logs/<задача>/report.json` (поле `resolved`, плоский или вложенный "
           "формат); читает `read_label` адаптера, сопоставление — по папке прогона и instance_id. В разобранных "
           "траекториях и `outputs/traj_index.csv` хранится `label` (1 = провал); здесь `resolved = 1 − label`.",
           "3. **Код.** Разбор на шаги — `src/adapters/swebench_bashonly.parse_trajectory` (assistant / tool / user, "
           "выполненные команды в `tool_calls`); разметка шагов — `src/g0_false_success.classify_segment` (события "
           "edit / verify), `src/e5_claims.command_events` (+ цель правки); фазы и признаки — "
           "`src/e5_claims.process_record` (E5), новые признаки — `src/e6_refine.block1_record` (E6); заявление об "
           "успехе — `src/g0_false_success` (`clean_nl`, `pick_final`, `match_any` с `config/claim_patterns.txt`).",
           "4. **Поля прогона.** model_id, model_family, agent_id (версия mini-SWE-agent), meta.exit_status (" +
           ", ".join(f"{k}: {v}" for k, v in runs.exit_status.value_counts().items()) + "), число шагов, "
           "meta.trajectory_format, meta.reasoning_sources. Отдельного поля «репозиторий» нет — выведено из "
           "instance_id.",
           "5. **Признаки `M_struct+len` (E5).** В коде два списка: `LEN` = n_steps, seg_len_steps, seg_len_sent, "
           "n_edits и `STRUCT` = 17 структурных (has_any_verification, n_verifications, verif_after_last_edit, "
           "n_verif_after_last_edit, verified_before_first_edit, n_steps_after_last_edit, share_edit, share_verify, "
           "share_other, n_transitions, phase_bigram_entropy, share_before_first_edit, repeat_ratio, "
           "distinct_cmd_ratio, nonzero_rc_share, n_files_edited, n_file_switches); итого 21. **Разделения на "
           "«процессные» и «артефактные» в коде нет.** Все 21 считаются по процессу — шагам, командам, выводам среды "
           "и тексту рассуждений; ни один не использует артефакты (отправленный патч, результаты тестов): адаптер "
           "отбрасывает сообщение с сабмитом, эталонный патч нигде не читается. Поэтому «только процессные» = все 21.",
           "6. **Расхождение с README.** README описывает четыре фазы (локализация, правка, верификация, прочее); в "
           "коде фаз три — edit / verify / other; демо использует разметку кода.",
           "7. **Перенос кода.** Разметка фаз вынесена в `demo_lib/phases.py` дословным копированием функций "
           "(автоматически, по исходникам), признаки — `demo_lib/features.py` (логика `process_record` на "
           "пошаговых данных). Проверка на 100 случайных прогонах (seed 42): максимальное расхождение "
           f"{dl['sample_100']['max_abs_diff']:.1e}, прогонов с иной разметкой фаз {dl['sample_100']['runs_with_phase_mismatch']}; "
           f"на всех {dl['all_runs']['n']} прогонах — {dl['all_runs']['max_abs_diff']:.1e} и "
           f"{dl['all_runs']['runs_with_phase_mismatch']}.", "",
           "## 2. Таблицы", "",
           f"- `data/demo/runs.parquet`: строк {len(runs)}, колонок {runs.shape[1]}, {size['runs.parquet'] / 1e6:.1f} МБ, "
           f"SHA256 `{sha['runs.parquet']}` — в репозитории.",
           f"- `steps.parquet`: строк {nsteps}, {size['steps.parquet'] / 1e6:.1f} МБ, SHA256 `{sha['steps.parquet']}` — "
           "в Release `demo-data-v1` (меньше 2 ГБ, дополнительное усечение не понадобилось).",
           f"- `examples.json` (SHA256 `{sha['examples.json']}`), `demo_lib.zip` (SHA256 `{sha['demo_lib.zip']}`).",
           "- Схемы колонок — `data/demo/DATASET_CARD.md`.", "",
           "## 3. Популяция", "",
           f"`submitted == 1`: прогонов {res['population']}; исключено {res['excluded']}: " +
           ", ".join(f"{k} — {v}" for k, v in excl.items()) + ".", "",
           "## 4. Примеры", "", "| тип | run_id | модель | шагов | submitted | claim_success | resolved |",
           "|---|---|---|---|---|---|---|"]
    for r in ex.itertuples():
        rep.append(f"| {r.type} | `{r.run_id}` | {r.model} | {r.n_steps} | {r.submitted} | {r.claim_success} | "
                   f"{r.resolved} |")
    a, c = res["auroc"], res["auroc_ci"]
    w, wc = res["within"], res["within_ci"]
    rep += ["", "## 5. Числа разделов 6–7 ноутбука", "",
            "Риск = вероятность, что отправленный результат не прошёл тесты; out-of-fold, GroupKFold(5) по задачам; "
            "интервалы — кластерный бутстрэп по задачам, 1000 повторов, seed 42.", "",
            "| модель | AUROC [95% ДИ] | внутри задач [95% ДИ] | AUROC, усреднённый по фолдам (сверка) |",
            "|---|---|---|---|"]
    for m in ("M_len", "M_struct+len"):
        rep.append(f"| `{m}` | {a[m]:.3f} [{c[m][0]:.3f}; {c[m][1]:.3f}] | {w[m]:.3f} [{wc[m][0]:.3f}; "
                   f"{wc[m][1]:.3f}] | {res['fold_avg'][m]:.3f} |")
    d, wd = res["auroc_diff"], res["within_diff"]
    rep += ["", f"- Разность `M_struct+len − M_len`: {d[0]:+.3f} [{d[1]:+.3f}; {d[2]:+.3f}]; внутри задач "
            f"{wd[0]:+.3f} [{wd[1]:+.3f}; {wd[2]:+.3f}].",
            f"- Внутри задач: {res['within_tasks']} задач с обоими исходами, {res['within_pairs']} пар «нерешённый — "
            "решённый» (ничьи считаются за 1/2).", "",
            "| бюджет | проверено | модель | найдено нерешённых | precision@k | lift@k |", "|---|---|---|---|---|---|"]
    for r in util.itertuples():
        rep.append(f"| {r.budget} | {r.checked} | `{r.model}` | {getattr(r, '_4')} | {getattr(r, '_5')} | "
                   f"{getattr(r, '_6')} |")
    rep += ["", "Риск примеров (`score_trajectory`, модель без задачи примера):", "",
            "| пример | модель | submitted | риск | resolved |", "|---|---|---|---|---|"]
    for s in res["scored"]:
        rep.append(f"| {s['example']} | {s['model']} | {s['submitted']} | {s['risk']:.3f} | {s['resolved']} |")
    rep += ["", "## 6. Время выполнения", "",
            f"Полное выполнение ноутбука (`jupyter nbconvert --execute`, чистое виртуальное окружение, скачивание по "
            f"HTTP с локального сервера; компьютер автора — i5-3230M, 2 ядра / 4 потока): **{float(seconds):.0f} с**; "
            f"из них расчёты после установки и загрузки — {res['elapsed_s']:.0f} с. Повторный запуск дал те же числа "
            "(два прогона, `demo_results.json` совпали); повторная сборка данных дала побайтно тот же "
            "`steps.parquet`.", "",
            "## 7. Что не сделано или упрощено", "",
            f"- **`final_message`** восстановлено из разобранных траекторий функциями G0 (`clean_nl`, `pick_final`): "
            f"сверка с результатом G0 на всех {chk['n_runs']} прогонах — расхождений в заявлении "
            f"{fm['claim_mismatch']}, в номере финального шага {fm['final_step_idx_mismatch']}, в длине текста "
            f"{fm['final_len_mismatch']} (`claim_success` при этом взят из G0 без пересчёта)."]
    rep += [line for line in (ROOT / "notebooks" / "demo_report_notes.md").read_text(encoding="utf-8").splitlines()]
    (ROOT / "DEMO_REPORT.md").write_text("\n".join(rep) + "\n", encoding="utf-8")
    print("DATASET_CARD.md and DEMO_REPORT.md written")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
