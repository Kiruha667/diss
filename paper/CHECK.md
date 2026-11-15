# Проверка статьи

Сгенерировано `python -m src.paper_check`. Индекс — `paper_kit/12_numbers_index.md`; текст — `paper/sections/*.tex`.

**Итог: всё сходится.**

- `\numsrc`: 26; ключ есть в индексе: 26; значение совпало с записью: 26 (текст точно — 20, числа точно, слова переведены — 6); не совпало: 0; неизвестных ключей: 0.
- Устаревших номеров строк индекса: 0.
- Чисел в тексте без `\numsrc`: 0.
- Ключей `\cite`: 7; нет в `paper/refs.bib`: 0; по `02_related_work.md` — только для диссертации: 0.
- Копии `refs.bib` и `figures/`: совпадают с paper_kit/.
- Открытых пометок [TODO]: 10.

Как сравнивается значение: LaTeX приводится к тексту (`$` убираются, `\,` и `~` — пробел, `\%` — %, `--` — «–», минусы одинаковы). «Текст точно» — строки совпали; «числа точно» — совпала последовательность чисел (те же числа, в том же порядке, с теми же знаками), а слова различаются: индекс на русском, статья на английском. Числа вне `\numsrc` ищутся в печатном тексте; не считаются номера разделов, рисунков и таблиц, годы, даты, идентификаторы arXiv и имена с цифрами (E15, T1, C3, Qwen3-32B, Opus 4.5).

## Открытые пометки [TODO]

- `paper/sections/00_front.tex:4`: [TODO: authors, affiliations, and submission metadata]
- `paper/sections/00_front.tex:8`: [TODO: abstract: the thesis of the kit in three or four sentences]
- `paper/sections/00_front.tex:12`: [TODO: keywords]
- `paper/sections/01_introduction.tex:7`: [TODO: text of the introduction]
- `paper/sections/03_decomposition.tex:8`: [TODO: text of the decomposition section and Table I]
- `paper/sections/04_attempt.tex:8`: [TODO: text of the attempt-level section]
- `paper/sections/05_words.tex:9`: [TODO: text of the section on the agent's words and Table III]
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
| `paper/sections/04_attempt.tex:16` | `e15k.dev.s2.ff` | 0.510 [0.459;\,0.557] | 0.510 [0.459; 0.557] | текст точно |
| `paper/sections/04_attempt.tex:17` | `e15k.both.s1.ff` | 0.534 [0.503;\,0.563] | 0.534 [0.503; 0.563] | текст точно |
| `paper/sections/04_attempt.tex:18` | `e15k.both.s2.ff` | 0.503 [0.471;\,0.533] | 0.503 [0.471; 0.533] | текст точно |
| `paper/sections/04_attempt.tex:19` | `e15k.dev.so2.ff` | 0.549 [0.502;\,0.593] | 0.549 [0.502; 0.593] | текст точно |
