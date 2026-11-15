# Проверка статьи

Сгенерировано `python -m src.paper_check` --no-fix. Индекс — `paper_kit/12_numbers_index.md`; текст — `paper/sections/*.tex`.

**Итог: всё сходится.**

- `\numsrc`: 103; ключ есть в индексе: 103; значение совпало с записью: 103 (текст точно — 93, числа точно, слова переведены — 10); не совпало: 0; неизвестных ключей: 0.
- Устаревших номеров строк индекса: 0 (не заменены: --no-fix).
- Чисел в тексте без `\numsrc`: 0.
- Ключей `\cite`: 7; нет в `paper/refs.bib`: 0; по `02_related_work.md` — только для диссертации: 0.
- Копии `refs.bib` и `figures/`: совпадают с paper_kit/.
- Открытых пометок [TODO]: 7.

Как сравнивается значение: LaTeX приводится к тексту (`$` убираются, `\,` и `~` — пробел, `\%` — %, `--` — «–», минусы одинаковы). «Текст точно» — строки совпали; «числа точно» — совпала последовательность чисел (те же числа, в том же порядке, с теми же знаками), а слова различаются: индекс на русском, статья на английском. Числа вне `\numsrc` ищутся в печатном тексте; не считаются номера разделов, рисунков и таблиц, годы, даты, идентификаторы arXiv и имена с цифрами (E15, T1, C3, Qwen3-32B, Opus 4.5).

## Открытые пометки [TODO]

- `paper/sections/00_front.tex:4`: [TODO: authors, affiliations, and submission metadata]
- `paper/sections/00_front.tex:8`: [TODO: abstract: the thesis of the kit in three or four sentences]
- `paper/sections/00_front.tex:12`: [TODO: keywords]
- `paper/sections/01_introduction.tex:7`: [TODO: text of the introduction]
- `paper/sections/06_transfer.tex:8`: [TODO: text of the transfer and limitations section]
- `paper/sections/07_related_conclusion.tex:7`: [TODO: text of the related work section]
- `paper/sections/07_related_conclusion.tex:12`: [TODO: text of the conclusion]

## Все `\numsrc`

| файл:строка | ключ | в статье | в индексе | совпадение |
|---|---|---|---|---|
| `paper/sections/02_data.tex:19` | `e15.ne.runs` | 7\,732 | 7 732 | текст точно |
| `paper/sections/02_data.tex:19` | `e15.ne.base` | 0.319 | 0.319 | текст точно |
| `paper/sections/02_data.tex:22` | `kth.runs_per_task` | 10 | 10 | текст точно |
| `paper/sections/02_data.tex:22` | `e8.qwen.runs` | 4247 | 4247 | текст точно |
| `paper/sections/02_data.tex:22` | `e8.dev.runs` | 4978 | 4978 | текст точно |
| `paper/sections/02_data.tex:27` | `e14.ne.runs` | 984 | 984 | текст точно |
| `paper/sections/02_data.tex:33` | `rev.s21` | 21 | 21 | текст точно |
| `paper/sections/02_data.tex:33` | `rev.s20` | 20 | 20 | текст точно |
| `paper/sections/02_data.tex:33` | `e10.o19` | 19 | 19 признаков | числа точно |
| `paper/sections/02_data.tex:33` | `m.lr.C` | $C=1$ | C=1 | текст точно |
| `paper/sections/02_data.tex:35` | `e0.folds` | 5 | 5 | текст точно |
| `paper/sections/02_data.tex:35` | `m.folds.t1` | 5 | 5 | текст точно |
| `paper/sections/02_data.tex:35` | `m.folds.t2` | 5 | n_splits=5 | числа точно |
| `paper/sections/02_data.tex:41` | `e15k.k` | $k=5$ | k = 5 | текст точно |
| `paper/sections/02_data.tex:41` | `e9.nsub` | 200 | 200 | текст точно |
| `paper/sections/02_data.tex:46` | `e16.pairs.all` | 1\,475 | 1 475 | текст точно |
| `paper/sections/02_data.tex:46` | `e16.pairs.new` | 595 | 595 | текст точно |
| `paper/sections/02_data.tex:49` | `kth.temp` | 0.6 | 0.6 | текст точно |
| `paper/sections/02_data.tex:49` | `e11b.temp` | 0 | temperature 0 | числа точно |
| `paper/sections/02_data.tex:49` | `e16.temp` | 0 | temperature 0 | числа точно |
| `paper/sections/02_data.tex:52` | `e15.ci` | 95\% | 95%, кластерный бутстрэп по задачам | числа точно |
| `paper/sections/02_data.tex:52` | `e8.boot` | 1000 resamples, seed 42 | 1000 выборок, seed 42 | числа точно |
| `paper/sections/03_decomposition.tex:9` | `e15.ne.runs` | $7\,732$ | 7 732 | текст точно |
| `paper/sections/03_decomposition.tex:9` | `e15.ne.D` | $0.926\,[0.912;\,0.936]$ | 0.926 [0.912; 0.936] | текст точно |
| `paper/sections/03_decomposition.tex:20` | `e15.ne.D` | $0.926\,[0.912;\,0.936]$ | 0.926 [0.912; 0.936] | текст точно |
| `paper/sections/03_decomposition.tex:20` | `e15.ne.D.p10` | $0.972\,[0.957;\,0.983]$ | 0.972 [0.957; 0.983] | текст точно |
| `paper/sections/03_decomposition.tex:20` | `e15.ne.D.rec` | $0.305$ | 0.305 | текст точно |
| `paper/sections/03_decomposition.tex:21` | `e15.ne.M` | $0.584\,[0.572;\,0.599]$ | 0.584 [0.572; 0.599] | текст точно |
| `paper/sections/03_decomposition.tex:21` | `e15.ne.M.within` | $0.734\,[0.707;\,0.763]$ | 0.734 [0.707; 0.763] | текст точно |
| `paper/sections/03_decomposition.tex:21` | `e15.ne.M.p10` | $0.445\,[0.410;\,0.488]$ | 0.445 [0.410; 0.488] | текст точно |
| `paper/sections/03_decomposition.tex:21` | `e15.ne.M.rec` | $0.140$ | 0.140 | текст точно |
| `paper/sections/03_decomposition.tex:22` | `e15.ne.MN` | $0.673\,[0.649;\,0.698]$ | 0.673 [0.649; 0.698] | текст точно |
| `paper/sections/03_decomposition.tex:22` | `e15.ne.MN.within` | $0.661\,[0.636;\,0.687]$ | 0.661 [0.636; 0.687] | текст точно |
| `paper/sections/03_decomposition.tex:22` | `e15.ne.MN.p10` | $0.578\,[0.522;\,0.633]$ | 0.578 [0.522; 0.633] | текст точно |
| `paper/sections/03_decomposition.tex:22` | `e15.ne.MN.rec` | $0.181$ | 0.181 | текст точно |
| `paper/sections/03_decomposition.tex:23` | `e15.ne.MLEN` | $0.675\,[0.652;\,0.697]$ | 0.675 [0.652; 0.697] | текст точно |
| `paper/sections/03_decomposition.tex:23` | `e15.ne.MLEN.within` | $0.667\,[0.641;\,0.693]$ | 0.667 [0.641; 0.693] | текст точно |
| `paper/sections/03_decomposition.tex:23` | `e15.ne.MLEN.p10` | $0.605\,[0.545;\,0.659]$ | 0.605 [0.545; 0.659] | текст точно |
| `paper/sections/03_decomposition.tex:23` | `e15.ne.MLEN.rec` | $0.190$ | 0.190 | текст точно |
| `paper/sections/03_decomposition.tex:24` | `e15.ne.S` | $0.654\,[0.630;\,0.676]$ | 0.654 [0.630; 0.676] | текст точно |
| `paper/sections/03_decomposition.tex:24` | `e15.ne.S.within` | $0.602\,[0.576;\,0.629]$ | 0.602 [0.576; 0.629] | текст точно |
| `paper/sections/03_decomposition.tex:24` | `e15.ne.S.p10` | $0.561\,[0.506;\,0.614]$ | 0.561 [0.506; 0.614] | текст точно |
| `paper/sections/03_decomposition.tex:24` | `e15.ne.S.rec` | $0.176$ | 0.176 | текст точно |
| `paper/sections/03_decomposition.tex:25` | `e15.ne.MS` | $0.678\,[0.653;\,0.701]$ | 0.678 [0.653; 0.701] | текст точно |
| `paper/sections/03_decomposition.tex:25` | `e15.ne.MS.within` | $0.658\,[0.632;\,0.685]$ | 0.658 [0.632; 0.685] | текст точно |
| `paper/sections/03_decomposition.tex:25` | `e15.ne.MS.p10` | $0.603\,[0.542;\,0.658]$ | 0.603 [0.542; 0.658] | текст точно |
| `paper/sections/03_decomposition.tex:25` | `e15.ne.MS.rec` | $0.189$ | 0.189 | текст точно |
| `paper/sections/03_decomposition.tex:30` | `e15.diff.DMS_DM.ne` | $+0.001\,[-0.000;\,+0.002]$ | +0.001 [−0.000; +0.002] | текст точно |
| `paper/sections/03_decomposition.tex:30` | `e15.diff.MS_MLEN.ne` | $+0.003\,[-0.003;\,+0.009]$ | +0.003 [−0.003; +0.009] | текст точно |
| `paper/sections/03_decomposition.tex:30` | `e15.diff.MS_MN.ne` | $+0.005\,[-0.003;\,+0.012]$ | +0.005 [−0.003; +0.012] | текст точно |
| `paper/sections/03_decomposition.tex:32` | `e15.lift.ne` | $1.89$ | 1.89 | текст точно |
| `paper/sections/03_decomposition.tex:32` | `e15.ne.base` | $0.319$ | 0.319 | текст точно |
| `paper/sections/03_decomposition.tex:32` | `e15.ne.MS.rec` | $0.189$ | 0.189 | текст точно |
| `paper/sections/03_decomposition.tex:32` | `e15.ne.maxrec` | $0.313$ | 0.313 | текст точно |
| `paper/sections/03_decomposition.tex:35` | `e7.ne.A` | $-0.008\,[-0.019;\,+0.002]$ | −0.008 [−0.019; +0.002] | текст точно |
| `paper/sections/03_decomposition.tex:35` | `e7.ne.R` | $+0.008\,[-0.016;\,+0.033]$ | +0.008 [−0.016; +0.033] | текст точно |
| `paper/sections/04_attempt_level.tex:8` | `e15k.dev.s1.all` | $0.581\,[0.545;\,0.618]$ | 0.581 [0.545; 0.618] | текст точно |
| `paper/sections/04_attempt_level.tex:8` | `e8.qwen.p2` | $0.530\,[0.489;\,0.571]$ | 0.530 [0.489; 0.571] | текст точно |
| `paper/sections/04_attempt_level.tex:8` | `e15k.dev.s1_nf` | $+0.037\,[-0.000;\,+0.076]$ | +0.037 [−0.000; +0.076] | текст точно |
| `paper/sections/04_attempt_level.tex:8` | `e15k.dev.s2_nf` | $+0.017\,[-0.021;\,+0.057]$ | +0.017 [−0.021; +0.057] | текст точно |
| `paper/sections/04_attempt_level.tex:8` | `e15k.both.s1_nf` | $+0.033\,[+0.004;\,+0.062]$ | +0.033 [+0.004; +0.062] | текст точно |
| `paper/sections/04_attempt_level.tex:8` | `e15k.both.s2_nf` | $+0.008\,[-0.021;\,+0.036]$ | +0.008 [−0.021; +0.036] | текст точно |
| `paper/sections/04_attempt_level.tex:14` | `e15k.dev.s2.ff` | $0.510\,[0.459;\,0.557]$ | 0.510 [0.459; 0.557] | текст точно |
| `paper/sections/04_attempt_level.tex:14` | `e15k.both.s1.ff` | $0.534\,[0.503;\,0.563]$ | 0.534 [0.503; 0.563] | текст точно |
| `paper/sections/04_attempt_level.tex:14` | `e15k.both.s2.ff` | $0.503\,[0.471;\,0.533]$ | 0.503 [0.471; 0.533] | текст точно |
| `paper/sections/04_attempt_level.tex:14` | `e15k.dev.so2.ff` | $0.549\,[0.502;\,0.593]$ | 0.549 [0.502; 0.593] | текст точно |
| `paper/sections/04_attempt_level.tex:18` | `e15k.qwen.s2.ff` | $0.498\,[0.456;\,0.539]$ | 0.498 [0.456; 0.539] | текст точно |
| `paper/sections/04_attempt_level.tex:18` | `e15k.dev.so_s_ff` | $+0.038\,[+0.011;\,+0.069]$ | +0.038 [+0.011; +0.069] | текст точно |
| `paper/sections/04_attempt_level.tex:18` | `e15k.dev.so_s_ff.t1` | $+0.020\,[-0.008;\,+0.050]$ | +0.020 [−0.008; +0.050] | текст точно |
| `paper/sections/04_attempt_level.tex:21` | `e15v.lat.task.pt` | $50.0\%$ | 50.0% | текст точно |
| `paper/sections/04_attempt_level.tex:21` | `e15v.lat.attempt.pt` | $8.6\%$ | 8.6% | текст точно |
| `paper/sections/04_attempt_level.tex:21` | `e15v.att.dev` | $11.6\%$ | 11.6% | текст точно |
| `paper/sections/04_attempt_level.tex:21` | `e15v.att.qwen` | $16.9\%$ | 16.9% | текст точно |
| `paper/sections/04_attempt_level.tex:21` | `e15v.obs.attempt` | $26.0\%\,[24.0;\,28.3]$ | 26.0% [24.0; 28.3] | текст точно |
| `paper/sections/04_attempt_level.tex:21` | `e15v.ceil.dev` | $1.000$ | 1.000 | текст точно |
| `paper/sections/04_attempt_level.tex:21` | `e15v.ceil.qwen` | $0.992$ | 0.992 | текст точно |
| `paper/sections/04_attempt_level.tex:24` | `e15k.k` | $k\,=\,5$ | k = 5 | текст точно |
| `paper/sections/04_attempt_level.tex:24` | `e15k.sel.dev.s1` | $+1.60\,[+0.55;\,+2.70]$ | +1.60 [+0.55; +2.70] | текст точно |
| `paper/sections/04_attempt_level.tex:24` | `e15k.sel.dev.s1fin` | $+0.71\,[-0.18;\,+1.68]$ | +0.71 [−0.18; +1.68] | текст точно |
| `paper/sections/04_attempt_level.tex:24` | `e15k.sel.dev.s2fin` | $+0.02\,[-0.94;\,+0.88]$ | +0.02 [−0.94; +0.88] | текст точно |
| `paper/sections/04_attempt_level.tex:24` | `e15k.sel.dev.so1fin` | $+1.12\,[+0.21;\,+2.06]$ | +1.12 [+0.21; +2.06] | текст точно |
| `paper/sections/04_attempt_level.tex:24` | `e15k.sel.dev.so2fin` | $+0.76\,[-0.07;\,+1.68]$ | +0.76 [−0.07; +1.68] | текст точно |
| `paper/sections/05_agent_words.tex:8` | `g0.ne.claim.fail` | $37.1\%$ | 37.1% | текст точно |
| `paper/sections/05_agent_words.tex:8` | `g0.ne.claim.ok` | $35.6\%$ | 35.6% | текст точно |
| `paper/sections/05_agent_words.tex:8` | `g0.hc.n` | $100$ | 100 | текст точно |
| `paper/sections/05_agent_words.tex:8` | `e15.all.runs` | $7\,864$ | 7 864 | текст точно |
| `paper/sections/05_agent_words.tex:8` | `rev.g0.power` | $24\,\mathrm{pp}$ | 24 п.п. | числа точно |
| `paper/sections/05_agent_words.tex:20` | `g0.ne.diff` | $-1.5\,\mathrm{pp}\,[-3.9;\,+0.9]$ | −1.5 п.п. [−3.9; +0.9] | числа точно |
| `paper/sections/05_agent_words.tex:21` | `g0.ne.within` | $+3.1\,\mathrm{pp}\,[+0.9;\,+5.3]$ | +3.1 п.п. [+0.9; +5.3] | числа точно |
| `paper/sections/05_agent_words.tex:22` | `g0.hc.strict` | $-0.157\,[-0.321;\,+0.017]$ | −0.157 [−0.321; +0.017] | текст точно |
| `paper/sections/05_agent_words.tex:23` | `g0.hc.broad` | $-0.057\,[-0.208;\,+0.094]$ | −0.057 [−0.208; +0.094] | текст точно |
| `paper/sections/05_agent_words.tex:26` | `e11b.c4` | $0.518\,[0.469;\,0.562]$ | 0.518 [0.469; 0.562] | текст точно |
| `paper/sections/05_agent_words.tex:27` | `e11b.c4_c3` | $-0.054\,[-0.103;\,+0.000]$ | −0.054 [−0.103; +0.000] | текст точно |
| `paper/sections/05_agent_words.tex:28` | `e16.all` | $0.551\,[0.520;\,0.584]$ | 0.551 [0.520; 0.584] | текст точно |
| `paper/sections/05_agent_words.tex:29` | `e16.new` | $0.534\,[0.489;\,0.581]$ | 0.534 [0.489; 0.581] | текст точно |
| `paper/sections/05_agent_words.tex:30` | `e16.c3_so` | $+0.005\,[-0.045;\,+0.055]$ | +0.005 [−0.045; +0.055] | текст точно |
| `paper/sections/05_agent_words.tex:31` | `e16.c3_g0` | $+0.014\,[-0.028;\,+0.055]$ | +0.014 [−0.028; +0.055] | текст точно |
| `paper/sections/05_agent_words.tex:36` | `e11b.pairs` | $112$ | 112 | текст точно |
| `paper/sections/05_agent_words.tex:36` | `e16.pairs.all` | $1\,475$ | 1 475 | текст точно |
| `paper/sections/05_agent_words.tex:36` | `e16.pairs.all` | $1\,475$ | 1 475 | текст точно |
| `paper/sections/05_agent_words.tex:36` | `e15k.dev.pairs.ff` | $1\,493$ | 1 493 | текст точно |
| `paper/sections/05_agent_words.tex:36` | `e16.pairs.new` | $595$ | 595 | текст точно |
| `paper/sections/05_agent_words.tex:36` | `e16.mostly` | $95\,\mathrm{or}\,100$ | 95 или 100 | числа точно |
