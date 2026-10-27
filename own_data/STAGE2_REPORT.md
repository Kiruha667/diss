# Этап 2 — пилот (протокол 1.1)

glm-4.6 (z.ai, thinking disabled), mini-swe-agent 1.17.1, конфиг `config/glm-4.6_v1.1.yaml`; 5 задач × 3 повтора строго по очереди. Выполнено прогонов: 15 из 15. Метка — swebench 4.1.0 (`resolved`). Журнал — `LOG.md`.

## Критерии (PROTOCOL.md, раздел 3)

| критерий | порог | значение | итог |
|---|---|---|---|
| Прогоны без технических сбоев | ≥ 14 из 15 (не больше 1 сбоя) | 15 из 16 попыток; сбоев 1 (прерванных попыток 1) | **выполнен** |
| Траектории, разобранные адаптером без ошибок | 15 из 15 | 15 из 15 | **выполнен** |
| Метка получена | 15 из 15 | 15 из 15 | **выполнен** |
| Медианная стоимость прогона | ≤ $0.20 | $0.0492 | **выполнен** |
| Суммарный расход пилота | < $5 (жёсткая остановка) | $1.0525 | **выполнен** |
| Задачи со смешанным исходом | ≥ 1 из 5 | 2 из 5 | **выполнен** |
| Доля решённых (отправленные прогоны) | [0.114; 0.514] | 0.800 (12 из 15) | **не выполнен** |
| Медиана длины (отправленные прогоны) | [25.9; 48.1] шага | 34.0 | **выполнен** |

Итог: выполнено 7 из 8 критериев.

Прерванные попытки (технические сбои, остатки в `raw_failed/`; прогон повторён под тем же run_id): `own-v1.1_mini-v1.17.1_glm-4.6_pilot_r2_pylint-dev__pylint-6386-attempt1`. Их расход по локальным данным неизвестен (траектория не сохранилась) и в суммы ниже не входит.

## Исходы по задачам

Ячейка — resolved (1 / 0), число шагов, стоимость; статус выхода, если не `Submitted`. «Корпус» — единственный прогон glm-4.6 на этой задаче в корпусе лидерборда.

| задача | повтор 0 | повтор 1 | повтор 2 | смешанный исход | glm-4.6 в корпусе |
|---|---|---|---|---|---|
| `pytest-dev__pytest-7324` | 1, 34 ш., $0.063 | 1, 34 ш., $0.090 | 1, 34 ш., $0.110 | нет | 0 |
| `psf__requests-1766` | 1, 11 ш., $0.017 | 1, 8 ш., $0.009 | 1, 13 ш., $0.014 | нет | 0 |
| `pylint-dev__pylint-6386` | 1, 70 ш., $0.164 | 0, 91 ш., $0.240 | 0, 33 ш., $0.051 | да | 1 |
| `sympy__sympy-23950` | 1, 28 ш., $0.036 | 1, 42 ш., $0.072 | 1, 38 ш., $0.049 | нет | 1 |
| `django__django-14376` | 0, 34 ш., $0.049 | 1, 32 ш., $0.046 | 1, 28 ш., $0.041 | да | 0 |

## Риски score_trajectory

Риск — вероятность нерешённой задачи по модели демо-ноутбука (логистическая регрессия на 21 признаке `M_struct+len`, обучена на корпусе без задачи прогона). При 15 прогонах — только описательно.

| прогон | задача | resolved | риск |
|---|---|---|---|
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r0_django_...
task_id                                       django__django-14376
repeat                                                           0
exit_status                                              Submitted
cost                                                      0.049177
parsed                                                        True
label                                                            1
parse_reason                                                  None
n_steps                                                         34
submitted                                                        1
resolved                                                         0
claim_success                                                    0
risk                                                      0.186987
Name: 0, dtype: object> | `django__django-14376` | 0 | 0.187 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r1_django_...
task_id                                       django__django-14376
repeat                                                           1
exit_status                                              Submitted
cost                                                      0.045726
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                         32
submitted                                                        1
resolved                                                         1
claim_success                                                    1
risk                                                      0.194617
Name: 5, dtype: object> | `django__django-14376` | 1 | 0.195 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r2_django_...
task_id                                       django__django-14376
repeat                                                           2
exit_status                                              Submitted
cost                                                      0.041129
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                         28
submitted                                                        1
resolved                                                         1
claim_success                                                    0
risk                                                      0.154133
Name: 10, dtype: object> | `django__django-14376` | 1 | 0.154 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r0_psf__re...
task_id                                         psf__requests-1766
repeat                                                           0
exit_status                                              Submitted
cost                                                      0.017391
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                         11
submitted                                                        1
resolved                                                         1
claim_success                                                    1
risk                                                      0.300159
Name: 1, dtype: object> | `psf__requests-1766` | 1 | 0.300 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r1_psf__re...
task_id                                         psf__requests-1766
repeat                                                           1
exit_status                                              Submitted
cost                                                      0.008945
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                          8
submitted                                                        1
resolved                                                         1
claim_success                                                    1
risk                                                      0.183906
Name: 6, dtype: object> | `psf__requests-1766` | 1 | 0.184 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r2_psf__re...
task_id                                         psf__requests-1766
repeat                                                           2
exit_status                                              Submitted
cost                                                      0.014226
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                         13
submitted                                                        1
resolved                                                         1
claim_success                                                    1
risk                                                      0.330569
Name: 11, dtype: object> | `psf__requests-1766` | 1 | 0.331 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r0_pylint-...
task_id                                    pylint-dev__pylint-6386
repeat                                                           0
exit_status                                              Submitted
cost                                                       0.16445
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                         70
submitted                                                        1
resolved                                                         1
claim_success                                                    1
risk                                                      0.216384
Name: 2, dtype: object> | `pylint-dev__pylint-6386` | 1 | 0.216 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r1_pylint-...
task_id                                    pylint-dev__pylint-6386
repeat                                                           1
exit_status                                              Submitted
cost                                                      0.239928
parsed                                                        True
label                                                            1
parse_reason                                                  None
n_steps                                                         91
submitted                                                        1
resolved                                                         0
claim_success                                                    1
risk                                                      0.356651
Name: 7, dtype: object> | `pylint-dev__pylint-6386` | 0 | 0.357 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r2_pylint-...
task_id                                    pylint-dev__pylint-6386
repeat                                                           2
exit_status                                              Submitted
cost                                                      0.051454
parsed                                                        True
label                                                            1
parse_reason                                                  None
n_steps                                                         33
submitted                                                        1
resolved                                                         0
claim_success                                                    1
risk                                                      0.247345
Name: 12, dtype: object> | `pylint-dev__pylint-6386` | 0 | 0.247 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r0_pytest-...
task_id                                    pytest-dev__pytest-7324
repeat                                                           0
exit_status                                              Submitted
cost                                                      0.063146
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                         34
submitted                                                        1
resolved                                                         1
claim_success                                                    1
risk                                                      0.138203
Name: 3, dtype: object> | `pytest-dev__pytest-7324` | 1 | 0.138 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r1_pytest-...
task_id                                    pytest-dev__pytest-7324
repeat                                                           1
exit_status                                              Submitted
cost                                                        0.0898
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                         34
submitted                                                        1
resolved                                                         1
claim_success                                                    1
risk                                                      0.247595
Name: 8, dtype: object> | `pytest-dev__pytest-7324` | 1 | 0.248 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r2_pytest-...
task_id                                    pytest-dev__pytest-7324
repeat                                                           2
exit_status                                              Submitted
cost                                                      0.110176
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                         34
submitted                                                        1
resolved                                                         1
claim_success                                                    1
risk                                                      0.162394
Name: 13, dtype: object> | `pytest-dev__pytest-7324` | 1 | 0.162 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r0_sympy__...
task_id                                         sympy__sympy-23950
repeat                                                           0
exit_status                                              Submitted
cost                                                      0.036387
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                         28
submitted                                                        1
resolved                                                         1
claim_success                                                    1
risk                                                      0.170974
Name: 4, dtype: object> | `sympy__sympy-23950` | 1 | 0.171 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r1_sympy__...
task_id                                         sympy__sympy-23950
repeat                                                           1
exit_status                                              Submitted
cost                                                      0.071893
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                         42
submitted                                                        1
resolved                                                         1
claim_success                                                    0
risk                                                      0.249016
Name: 9, dtype: object> | `sympy__sympy-23950` | 1 | 0.249 |
| r<bound method Series.repeat of run_id           own-v1.1_mini-v1.17.1_glm-4.6_pilot_r2_sympy__...
task_id                                         sympy__sympy-23950
repeat                                                           2
exit_status                                              Submitted
cost                                                      0.048684
parsed                                                        True
label                                                            0
parse_reason                                                  None
n_steps                                                         38
submitted                                                        1
resolved                                                         1
claim_success                                                    0
risk                                                       0.11943
Name: 14, dtype: object> | `sympy__sympy-23950` | 1 | 0.119 |

- Средний риск: нерешённые 0.264, решённые 0.206.
- Пары «нерешённый — решённый» внутри одной задачи: 4; нерешённый получил больший риск в 3 из них (0.75).

## Расход

- Пилот: $1.0525 (медиана прогона $0.0492, максимум $0.2399).
- До пилота: smoke 1.0 $0.0127 и проба $0.0198. Всего с ключа: $1.0851.
