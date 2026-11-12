# 02. Связанные работы

Источник — `docs/LITERATURE.md` (страницы arXiv прочитаны; PDF с числами не сверялся — правило автора: сверить перед
подачей). Числа чужих работ взяты из `docs/LITERATURE.md` и помечены ссылкой на него. Ключи BibTeX — в
`references.bib`; авторы и названия — со страниц arXiv (API arXiv, 12.11.2026). Работы, которые в `docs/LITERATURE.md`
помечены «не прочитано» или «как прямой аналог не цитировать» (StopLoss 2610.25525, Cross-Task Behavioral
Consistency 2608.13598, работы из ссылок Insecure Reporters), в набор не включены.

## 1. Позиционирование: две ближайшие работы

### arXiv 2611.08285 — Shi, Assunção, «Hard Issue, Weak Model, or Bad Run?» (`shi2026hardissue`), параллельная работа

| | 2611.08285 | наша работа |
|---|---|---|
| когда | подана 05.11.2026 | разложение E7 закоммичено 03.11.2026 (`4728b46`), до её подачи |
| бенчмарк и каркас | SWE-bench Verified, mini-swe-agent | то же (лидерборд bash-only) + повторы ASSERT-KTH (nano-agent) |
| что предсказывается | провал по **префиксу** идущего прогона (10, 20, 40 шагов) [источник: docs/LITERATURE.md, стр. 8] | недостоверность **сданного** результата по **полной** траектории после отправки |
| сложность задачи по другим моделям | 0.924 [источник: docs/LITERATURE.md, стр. 8] общий AUROC | 0.926 [0.912; 0.936] [источник: reports/E15_REPORT.md, стр. 51] (потолок, к новым задачам не применим) |
| модель внутри задачи | 0.724 [источник: docs/LITERATURE.md, стр. 8] | 0.734 [0.707; 0.763] [источник: reports/E15_REPORT.md, стр. 52] |
| траектория сверх «задача + модель» | «equivalent to zero» во всех конфигурациях [источник: docs/LITERATURE.md, стр. 8] | +0.001 [−0.000; +0.002] [источник: reports/E15_REPORT.md, стр. 96] |
| оценка внутри единицы на повторах одной модели | нет (пилот разложения на Llama) | есть: повторы KTH, две настройки (`06_results_run_level.md`) |
| законченные и оборванные попытки | нет | есть |
| самооценка агента, исходы его проверок | нет | есть (E10, E11, E11b, E16) |
| перенос между форматами каркаса | нет | есть (E13, E14) |

**Как писать.** Concurrent work that reaches the same decomposition on the same benchmark; our decomposition (E7) was
committed before its submission, but we do not claim priority. What we add: post-submission assessment on full
trajectories, the within-unit level on repeated runs, the finished/cut-off split, the agent's own words and checks,
and transfer across scaffold log formats. Не писать «мы первыми показали разложение».

### arXiv 2610.05572 — «Disentangling Task Difficulty from Run-Level Failure in Agent Failure Prediction» (`esfandyaridoulabi2026disentangling`)

| | 2610.05572 | наша работа |
|---|---|---|
| главная мысль | общий AUROC почти целиком — различия пар «модель — задача»; нужна оценка внутри единицы | то же подтверждаем на SWE-bench (E7, E15) и проверяем внутри единицы на повторах |
| сигнал внутри единицы на SWE-bench | поведенческие признаки «reach 0.656 only on full trajectories» [источник: docs/LITERATURE.md, стр. 9] | у законченных попыток структура не отличается от случайной; на всех парах devstral 0.561 [0.522; 0.598] [источник: reports/E15_REPORT.md, стр. 142] (обучение внутри настройки) |
| сила модели и предсказуемость | −0.662 [источник: docs/LITERATURE.md, стр. 9] на LiveClawBench, для AUROC внутри единицы | зависит от определения; с этим числом напрямую не сравнимо (`08_transfer_limitations.md`) |
| ранняя остановка | пороги полезности остановки | не изучаем: оценка после отправки |

**Как писать.** We follow its call to report within-unit AUROC and add: finished-vs-cut-off attempts, the agent's own
words and check outcomes, and a variance decomposition with an evaluation-noise ceiling. Числа 0.841 и 0.786 [источник: docs/LITERATURE.md, стр. 9] — не её,
а Ge et al.; не приписывать.

## 2. Остальные работы

| работа (ключ) | что показала | как соотносится с нами | раздел статьи (`11_paper_outline.md`) |
|---|---|---|---|
| Jimenez et al., SWE-bench (`jimenez2024swebench`) | бенчмарк задач из GitHub со скрытыми тестами | источник задач и меток | §2 |
| Yang et al., SWE-agent (`yang2024sweagent`) | каркас агента; mini-SWE-agent — его упрощённый вариант | каркас корпуса лидерборда | §2 |
| Bjarnason, Silva, Monperrus, On Randomness in Agentic Evals (`bjarnason2026randomness`) | повторные прогоны на SWE-bench Verified; набор ASSERT-KTH | источник повторов; метки одной настройки исключены (`03_data.md`) | §2 |
| Ge et al., Agent psychometrics (`ge2026psychometrics`) | сложность задачи предсказуема по описанию (IRT по многим агентам) | наше представление описания сверх `M+S` ничего не добавляет (E12) | §3; §7 |
| Mehtiyev, Assunção, Beyond Resolution Rates (`mehtiyev2026beyond`) | поведение агента в основном определяется LLM | объясняет смешение сигнала траектории с моделью | §3 |
| Shu et al., What Resolve Rate Hides (`shu2026resolverate`) | антипаттерны траекторий во многом — признак сложности корпуса | обоснование анализа внутри задачи | §3 |
| Cho et al., Automata from Agent Traces (`cho2026automata`) | автоматы по трассам; без группировки по задачам | ориентир сверху; слабее протокол | §1; §7 |
| EarlyEval (`shi2026earlyeval`) | ранний прогноз исхода для дешёвой оценки | ранний прогноз, не оценка после отправки | §7 |
| Fail-Fast, Restart-Smart (`wang2026failfast`) | пошаговый монитор и перезапуск на SWE-bench Verified | ранний прогноз; другой вопрос | §7 |
| SWE-Gym (`pan2025swegym`), R2E-Gym (`jain2025r2egym`), SWE-RM (`shum2025swerm`) | обученные верификаторы по траектории и патчу; выигрыш при выборе из k | постановка «достоверность сданного результата» не нова; наш отрицательный выбор из k относится к признакам структуры, а не к задаче выбора в целом | §4; §7 |
| Kaddour et al., Agentic Uncertainty (`kaddour2026agentic`) | отдельный агент оценивает успех по патчу после выполнения; слабое различение | ближе всего к нашей самооценке; у нас — тот же агент в своём контексте, внутри единицы | §5 |
| Huang et al., «Insecure» Reporters (`huang2026insecure`) | на синтетических логах «Be honest» резко повышает упоминание дефекта | исходная идея E11; на реальных траекториях эффект не воспроизвёлся | §5 |
| Khullar et al., Self-Attribution Bias (`khullar2026selfattribution`) | монитор оценивает собственные патчи с высоким общим AUROC | ограничивает общность: наш вывод — внутри единицы, у одной модели | §5 |
| Advani, From Confident Closing to Silent Failure (`advani2026confident`) | ложный успех частый; LLM-судьи слабые | контекст ложного успеха; диалоговые домены | §1 |
| Mehta, Confident and Wrong (`mehta2026confident`) | тихие семантические провалы кодовых агентов | ближайший по явлению; модели оценки нет | §1 |
| Li et al., Last Step Matters (`li2026laststep`) | запрошенная уверенность в конце работы информативна в deep research | контраст: у нас запрошенная уверенность почти не различает законченные попытки | §5 |
| Chen et al., Agent-Generated Tests (`chen2026agenttests`) | на тех же данных лидерборда поведение с тестами почти не связано с исходом | согласуется с E10 (исходы проверок ничего не добавляют между моделями) | §4 |

Не найдено (рецензия, `review/REVIEW.md`, §6): работа, которая спрашивает вероятность успеха у того же кодового
агента в его контексте и оценивает внутри единицы; работа, которая сравнивает частоту заявлений в финальных сообщениях
решённых и нерешённых прогонов лидерборда SWE-bench. Формулировать как «we are not aware of», не «first».
