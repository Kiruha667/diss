# Диагностика расхождения доли решённых в пилоте

Пилот (STAGE2_REPORT.md): glm-4.6 решила 12 из 15 прогонов на 5 задачах, а прогон той же модели в корпусе
(`20251201_mini-v1.17.1_glm-4.6`) — 2 из 5. Диагностика без вызовов LLM API; основной сбор не начинался.
Скрипты и данные — `own_data/diag/` (`fetch_corpus.py`, `analyze_corpus.py`, `eval_corpus.sh`, `compare.py`,
`docker_facts.sh`; результаты — `corpus_glm46/`, `compare.json`, `docker_facts.json`).

## 1. Скачанные артефакты корпуса

Источник — тот же, что у корпуса: публичный бакет `swe-bench-submissions`, префикс
`bash-only/20251201_mini-v1.17.1_glm-4.6` (из `metadata.yaml` прогона), код загрузки адаптера. Для каждой из 5 задач:
траектория (`trajs/<задача>/<задача>.traj.json`), итоговый патч (`info.submission` → `submission.diff`) и всё из
`logs/<задача>/`: `report.json`, `test_output.txt`, `patch.diff`, `job-output.json`. Файла `run_instance.log` в
источнике нет. `patch.diff` лидерборда совпадает с `info.submission` у всех 5 задач.

Проверка на ключи перед сохранением: в каждой траектории одно поле `model.model_kwargs.api_key` с ключом того, кто
запускал прогон, — заменено на `<REDACTED>` (5 замен); других совпадений с форматами ключей нет; ключ этой машины не
встречается.

## 2. Оценка лидерборда по артефактам

| задача | resolved | патч применён | FAIL_TO_PASS: прошли / упали | PASS_TO_PASS упали | тайм-аут | признаки сбоя окружения |
|---|---|---|---|---|---|---|
| `pytest-dev__pytest-7324` | false | да | 1 / 2 | 0 | нет | нет |
| `psf__requests-1766` | false | да | 1 / 5 | 18 | нет | **httpbin.org отвечал 503 «Service Temporarily Unavailable»** (55 строк с 503 в выводе тестов; упали 28 из 91 теста) |
| `pylint-dev__pylint-6386` | true | да | 1 / 0 | 0 | нет | нет |
| `sympy__sympy-23950` | true | да | 1 / 0 | 0 | нет | нет |
| `django__django-14376` | false | да | 0 / 3 | 0 | нет | нет |

Упавшие тесты:
- `pytest-dev__pytest-7324`: `test_valid_idents[False]`, `test_valid_idents[None]` — `AssertionError` в самом тесте
  (`assert evaluate(ident, …)` получает `False` / `None`); ошибок окружения нет.
- `psf__requests-1766`: FAIL_TO_PASS — `test_HTTP_200_OK_GET_ALTERNATIVE`, `test_cookie_sent_on_redirect`,
  `test_custom_content_type`, `test_params_are_merged_case_sensitive`, `test_user_agent_transfers`; PASS_TO_PASS — 18
  тестов (`test_HTTP_200_OK_HEAD`, `test_HTTP_200_OK_PUT`, `test_session_pickling` и др.). Все падения — `assert 503`
  или их следствия (`KeyError`, ошибка разбора JSON: вместо ответа httpbin пришла HTML-страница 503). Единственный
  FAIL_TO_PASS-тест без сети (`test_DIGESTAUTH_QUOTES_QOP_VALUE`) прошёл.
- `django__django-14376`: `test_options_non_deprecated_keys_preferred`, `test_options_override_settings_proper_values`,
  `test_parameters` (`dbshell.test_mysql.MySqlDbshellCommandTestCase`); ошибок окружения нет.

Ошибок установки зависимостей и неприменённых патчей нет ни в одной задаче (`patch_successfully_applied: true`,
`job-output.json`: `timed_out: false`).

## 3. Корпусные патчи в нашей оценке

swebench 4.1.0, те же образы, что в пилоте, `run_id diag_corpus_glm46`.

| задача | resolved в корпусе | resolved у нас (тот же патч) | разошедшиеся тесты |
|---|---|---|---|
| `pytest-dev__pytest-7324` | 0 | 0 | нет |
| `psf__requests-1766` | 0 | **1** | 23: 5 FAIL_TO_PASS + 18 PASS_TO_PASS, все «упал у лидерборда → прошёл у нас» |
| `pylint-dev__pylint-6386` | 1 | 1 | нет |
| `sympy__sympy-23950` | 1 | 1 | нет |
| `django__django-14376` | 0 | 0 | нет |

**`psf__requests-1766` отдельно.** В `test_requests.py` 52 теста обращаются к `httpbin(...)` с адресом по умолчанию
`http://httpbin.org/` (`HTTPBIN = os.environ.get('HTTPBIN_URL', 'http://httpbin.org/')`): из них 5 из 6 тестов
FAIL_TO_PASS и 41 из 79 PASS_TO_PASS этой задачи. Все 23 разошедшихся теста — сетевые. В нашей оценке у контейнера был
доступ в сеть: swebench 4.1.0 создаёт контейнер без сетевых параметров (`client.containers.create(...)` без
`network_disabled` / `network_mode`, сеть Docker по умолчанию), сетевые тесты в оценке корпусного патча и трёх патчей
пилота прошли без ответов 503, а проба из контейнера той же задачи (2026-10-27 13:25 UTC) получила от
`http://httpbin.org/get` код 200.

## 4. Патчи корпуса и пилота там, где метки совпали

Размер диффа включает вспомогательные скрипты, которые агент оставил в корне репозитория; подход оценён по
изменениям в исходниках проекта.

| задача | корпус | пилот (3 повтора) | похожий ли подход |
|---|---|---|---|
| `pytest-dev__pytest-7324` (корпус 0, пилот 3/3) | `src/_pytest/mark/expression.py`, `not_expr`: для `True`/`False`/`None` возвращает `ast.NameConstant(eval(...))` (+2 строки в исходнике; +75 с тремя скриптами) | тот же файл и функция, но идентификатор подменяется служебным именем и восстанавливается в `MatcherAdapter` (+8, +12, +21/−2 в исходнике) | **нет**: корпус превращает идентификатор в константу — именно это ломает `test_valid_idents[False/None]`; все три патча пилота используют другой подход |
| `django__django-14376` (корпус 0, пилот 2/3) | `mysql/base.py`: `db`→`database`, `passwd`→`password` (+2/−2) | та же правка `base.py` во всех трёх + правка `mysql/client.py`; решены r1, r2 (оставлен запасной ключ `db`), не решён r0 (запасной `passwd` удалён) | **частично**: общий шаг одинаковый, но корпус не тронул `client.py`, из-за чего падают тесты dbshell |
| `pylint-dev__pylint-6386` (корпус 1, пилот 1/3) | `config/utils.py`: `-v` в `PREPROCESSABLE_OPTIONS` и разбор коротких опций в `_preprocess_options` (+16/−6) | r0 (решён): `-v` в `PREPROCESSABLE_OPTIONS` + `nargs=0` в `_DoNothingAction`; r1 (не решён): новое действие в `callback_actions.py` и `base_options.py`; r2 (не решён): только `nargs=0` в `_DoNothingAction` | **разные подходы**; решённый r0 совпадает с корпусом в ключевом шаге (`-v` в `PREPROCESSABLE_OPTIONS`) |
| `sympy__sympy-23950` (корпус 1, пилот 3/3) | `sets/contains.py`, `as_set`: `return self.args[1]` вместо `NotImplementedError` (+6/−1; +291 с восемью скриптами) | то же исправление (+1/−1, +1/−1, +12/−1) | **да**: одинаковое исправление |

## 5. Образы задач

Digest и дата создания образов, которыми пользовались пилот и эта диагностика (`docker image inspect`):

| задача | digest | создан (UTC) |
|---|---|---|
| `pytest-dev__pytest-7324` | `sha256:bbdb6c6576dc1294ac789dd5ab3bed3ae231aec39c2d4f3d643bab76a4ef34ff` | 2026-08-13 03:13 |
| `psf__requests-1766` | `sha256:0618949b48408350dfae74d27e47ff9df8192fbf03248aa4f10650e0a36f52eb` | 2026-08-13 02:46 |
| `pylint-dev__pylint-6386` | `sha256:fda08a7a0a711af33163d53ed09b8401d84b7c9bb76b31e25a81357c98e6aeed` | 2026-08-13 03:07 |
| `sympy__sympy-23950` | `sha256:3332a07cc5df8ca74a7b48b958155cb8f3ceed2e7ccebde20fcea6930a69562f` | 2026-08-13 04:03 |
| `django__django-14376` | `sha256:cedafc3a8b3c781cf4ab59095146e5f470af9c91e0a45845d9f8a8cfb971d55d` | 2026-08-13 01:26 |

Все образы пересобраны после корпусного прогона (2025-12-01). **Digest образов, на которых получен корпус, неизвестен**:
конфиг корпуса указывает только тег `:latest`.

## 6. Вывод

**Исход B: метки воспроизводятся, расходятся решения модели** (детали: наша оценка воспроизводит 4 из 5 корпусных
меток на тех же патчах — pylint 1, sympy 1, pytest 0, django 0; исключение — `psf__requests-1766`: метка лидерборда 0
получена, когда httpbin.org отвечал 503, все 23 разошедшихся теста сетевые, наша оценка того же патча даёт 1; в
`pytest-dev__pytest-7324` и `django__django-14376` корпусный патч не проходит и у нас, а патчи пилота проходят в 3 из 3
и 2 из 3 повторов — другой подход в pytest, более полная правка в django; в `pylint-dev__pylint-6386` пилот решил 1 из 3
разными подходами, в `sympy__sympy-23950` решения совпадают. При пересчёте корпусных меток нашей оценкой корпус решил бы
3 из 5 задач против 12 из 15 прогонов пилота).
