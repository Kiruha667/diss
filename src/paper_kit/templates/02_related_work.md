# 02. Связанные работы

Источник — `docs/LITERATURE.md` (страницы arXiv прочитаны; PDF с числами не сверялся — правило автора: сверить перед
подачей). Числа чужих работ взяты из `docs/LITERATURE.md` и помечены ссылкой на него. Ключи BibTeX — в
`references.bib`; авторы и названия статей — со страниц arXiv (API arXiv, 12.11.2026). Источники, на которых стоит
статья, но которых нет на arXiv (SWE-bench Verified, mini-SWE-agent, лидерборд bash-only, набор KTH, issue #484,
Vals AI), — записи `@misc` с URL и датой обращения (13.11.2026). Описания работ сверены с аннотациями arXiv
(проверка набора, `review/PAPER_KIT_CHECK.md`, §3.2). Работы, которые в `docs/LITERATURE.md`
помечены «не прочитано» или «как прямой аналог не цитировать» (StopLoss 2610.25525, Cross-Task Behavioral
Consistency 2608.13598, работы из ссылок Insecure Reporters), в набор не включены.

## 1. Позиционирование: две ближайшие работы

### arXiv 2611.08285 — Shi, Assunção, «Hard Issue, Weak Model, or Bad Run?» (`shi2026hardissue`), параллельная работа

| | 2611.08285 | наша работа |
|---|---|---|
| когда | подана 05.11.2026 | разложение E7 закоммичено 03.11.2026 (`4728b46`), до её подачи |
| бенчмарк и каркас | SWE-bench Verified, mini-swe-agent | то же (лидерборд bash-only) + повторы ASSERT-KTH (nano-agent) |
| что предсказывается | провал по **префиксу** идущего прогона ({{lit.shi.prefixes|v}} шагов) {{src:lit.shi.prefixes}} | недостоверность **сданного** результата по **полной** траектории после отправки |
| сложность задачи по другим моделям | {{lit.shi.diff}} общий AUROC | {{e15.ne.D}} (потолок, к новым задачам не применим) |
| модель внутри задачи | {{lit.shi.model_within}} | {{e15.ne.M.within}} |
| траектория сверх «задача + модель» | «equivalent to zero» во всех конфигурациях {{src:lit.shi.zero}} | {{e15.diff.DMS_DM.ne}} |
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
| сигнал внутри единицы на SWE-bench | поведенческие признаки «reach 0.656 only on full trajectories» {{src:lit.esf.full}} | на всех парах devstral {{e15k.dev.s2.all}} (обучение внутри настройки); у законченных попыток — не больше очень слабого (`01`, У6). Напрямую не сравнимо: другие модели, признаки и популяция |
| сила модели и предсказуемость | {{lit.esf.rho}} на LiveClawBench, для AUROC внутри единицы | зависит от определения; с этим числом напрямую не сравнимо (`08_transfer_limitations.md`) |
| ранняя остановка | пороги полезности остановки | не изучаем: оценка после отправки |

**Как писать.** We follow its call to report within-unit AUROC and add: finished-vs-cut-off attempts, the agent's own
words and check outcomes, and a variance decomposition with an evaluation-noise ceiling. Числа {{lit.ge.841|v}} и {{lit.ge.786|v}} {{src:lit.ge.841}} — не её,
а Ge et al.; не приписывать.

## 2. Остальные работы

| работа (ключ) | что показала | как соотносится с нами | раздел статьи (`11_paper_outline.md`) |
|---|---|---|---|
| Jimenez et al., SWE-bench (`jimenez2024swebench`) | бенчмарк задач из GitHub со скрытыми тестами | источник задач и меток; статья — про исходный SWE-bench | §2 |
| OpenAI, SWE-bench Verified (`openai2024sweverified`) | подмножество тестовых задач SWE-bench, проверенных людьми | вариант бенчмарка, на котором стоит вся работа | §2 |
| mini-SWE-agent (`minisweagent2025`) | минимальный каркас агента (только bash) проекта SWE-agent | каркас корпуса лидерборда, версии v1.x и v2.0.0 | §2 |
| Лидерборд SWE-bench bash-only (`swebench2026bashonly`) | сравнение LLM на SWE-bench Verified с mini-SWE-agent; траектории и логи оценки в открытом бакете | источник корпуса | §2 |
| Yang et al., SWE-agent (`yang2024sweagent`) | каркас агента с интерфейсом «агент — компьютер» | **не каркас корпуса**: mini-SWE-agent — отдельный каркас того же проекта; SWE-agent цитировать только как контекст | §2 (по желанию) |
| Bjarnason, Silva, Monperrus, On Randomness in Agentic Evals (`bjarnason2026randomness`) | повторные прогоны на SWE-bench Verified | работа, ради которой опубликован набор повторов | §2 |
| Набор ASSERT-KTH (`assertkth2026artifacts`) | траектории и результаты оценки этой работы; ревизия закреплена | источник повторов; метки одной настройки исключены (`03_data.md`) | §2 |
| SWE-bench issue #484 (`swebench2025issue484`) | тесты psf/requests обращаются к внешнему httpbin.org, который временами отвечает ошибкой сервера (5xx) | шум меток psf/requests известен; у нас — количественная оценка на лидерборде (`03_data.md`, §4) | §2 или Limitations |
| Vals AI, SWE-bench Verified (`valsai2026swebench`) | бенчмарк помечен как архивный: результаты насыщены | насыщение бенчмарка (`08_transfer_limitations.md`, §2) | Limitations |
| Ge et al., Agent psychometrics (`ge2026psychometrics`) | сложность задачи предсказуема по описанию (IRT по многим агентам) | наше представление описания сверх `M+S` ничего не добавляет (E12) | §3; §7 |
| Mehtiyev, Assunção, Beyond Resolution Rates (`mehtiyev2026beyond`) | по аннотации: поведение агента в основном определяется LLM; связь длины с провалом «reverses direction once task difficulty is controlled»; «trajectory structure discriminates consistently» | первое объясняет смешение сигнала траектории с моделью; второе согласуется с нашими данными (число шагов внутри задачи не помогает); третье **расходится с У2** — в статье ответить: у нас структура сверх «модель + длина» и сверх сложности задачи ничего не добавляет | §3 |
| Shu et al., What Resolve Rate Hides (`shu2026resolverate`) | антипаттерны траекторий во многом — признак сложности корпуса | обоснование анализа внутри задачи | §3 |
| Cho et al., Automata from Agent Traces (`cho2026automata`) | автоматы по трассам для прогноза провала; по аннотации топология поведения «shaped more by the deployment harness than by the LLM» | к У17: признаки зависят от каркаса. «Без группировки по задачам» по аннотации не проверяется — сверить с текстом или не писать | §6; §7 |
| EarlyEval (`shi2026earlyeval`) | ранний прогноз исхода для дешёвой оценки | ранний прогноз, не оценка после отправки | §7 |
| Fail-Fast, Restart-Smart (`wang2026failfast`) | пошаговый монитор и перезапуск на SWE-bench Verified | ранний прогноз; другой вопрос | §7 |
| SWE-Gym (`pan2025swegym`), R2E-Gym (`jain2025r2egym`), SWE-RM (`shum2025swerm`) | обученные верификаторы по траектории и патчу; выигрыш при выборе из k | постановка «достоверность сданного результата» не нова; наш отрицательный выбор из k относится к признакам структуры, а не к задаче выбора в целом | §4; §7 |
| Kaddour et al., Agentic Uncertainty (`kaddour2026agentic`) | отдельный агент оценивает успех по патчу после выполнения; слабое различение | ближе всего к нашей самооценке; у нас — тот же агент в своём контексте, внутри единицы | §5 |
| Huang et al., «Insecure» Reporters (`huang2026insecure`) | на синтетических логах «Be honest» резко повышает упоминание заложенного дефекта в отчёте | исходная идея E11. **Другая задача**: у них — раскрытие дефекта в отчёте, у нас — различение исходов; на различение исходов просьба о честности не повлияла (У13). Не писать «эффект не воспроизвёлся» | §5 |
| Khullar et al., Self-Attribution Bias (`khullar2026selfattribution`) | монитор оценивает собственные патчи с высоким общим AUROC; главный вывод — оценка своей работы в том же контексте хуже, чем в новом | наш E11 — именно оценка в том же контексте; ограничивает общность: наш вывод — внутри единицы, у одной модели | §5 |
| Advani, From Confident Closing to Silent Failure (`advani2026confident`) | ложный успех частый; LLM-судьи слабые | контекст ложного успеха; диалоговые домены | §1 |
| Mehta, Confident and Wrong (`mehta2026confident`) | тихие семантические провалы кодовых агентов | ближайший по явлению; модели оценки нет | §1 |
| Li et al., Last Step Matters (`li2026laststep`) | запрошенная уверенность в конце работы информативна в deep research | контраст: у нас запрошенная уверенность почти не различает законченные попытки | §5 |
| Chen et al., Agent-Generated Tests (`chen2026agenttests`) | тот же лидерборд, другой набор моделей (шесть): внутри модели поведение с тестами «differs less between resolved and unresolved» | согласуется с E10 (исходы проверок ничего не добавляют между моделями) | §4 |

## 3. Что цитировать в статье (16 записей; план — `11_paper_outline.md`)

**В статье:** `shi2026hardissue`, `esfandyaridoulabi2026disentangling`, `jimenez2024swebench`, `openai2024sweverified`,
`minisweagent2025`, `swebench2026bashonly`, `bjarnason2026randomness`, `assertkth2026artifacts`,
`swebench2025issue484`, `mehtiyev2026beyond`, `kaddour2026agentic`, `khullar2026selfattribution`, `huang2026insecure`,
`chen2026agenttests`, `jain2025r2egym` (обученные верификаторы — одной ссылкой), `valsai2026swebench`.

**Только диссертация:** `shi2026earlyeval`, `wang2026failfast`, `cho2026automata`, `advani2026confident`,
`mehta2026confident`, `li2026laststep`, `pan2025swegym`, `shum2025swerm`, `yang2024sweagent`, `shu2026resolverate`,
`ge2026psychometrics` (E12 и У19 в статью не входят).

У работ с числом авторов больше шести в списке литературы статьи — «et al.» (стиль IEEE это допускает).

## 4. Чего нет в литературе

Не найдено (рецензия, `review/REVIEW.md`, §6): работа, которая спрашивает вероятность успеха у того же кодового
агента в его контексте и оценивает внутри единицы; работа, которая сравнивает частоту заявлений в финальных сообщениях
решённых и нерешённых прогонов лидерборда SWE-bench. Формулировать как «we are not aware of», не «first».
