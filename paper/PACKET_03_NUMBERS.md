# Пакет 03 — числовые ссылки и открытые вопросы

Основа — `bfc5074`. В реестре перечислено каждое новое вхождение `\numsrc`. Источник значений и интервалов — [индекс набора](../paper_kit/12_numbers_index.md).

| Место в статье | Ключ и строка индекса | Значение по индексу | Что измерено | Первичный отчёт |
|---|---|---|---|---|
| [00_front.tex, строка 6](sections/00_front.tex#L6) | [`e15.ne.MS.p10@L90`](../paper_kit/12_numbers_index.md#L90) | 0.603 [0.542; 0.658] | precision@10%, M+S, P_ne | [E15_REPORT.md, строка 56](../reports/E15_REPORT.md#L56) |
| [00_front.tex, строка 6](sections/00_front.tex#L6) | [`e15.lift.ne@L106`](../paper_kit/12_numbers_index.md#L106) | 1.89 | лифт precision@10% M+S, P_ne | [E15_REPORT.md, строка 84](../reports/E15_REPORT.md#L84) |
| [00_front.tex, строка 6](sections/00_front.tex#L6) | [`e15.diff.MS_MLEN.ne@L108`](../paper_kit/12_numbers_index.md#L108) | +0.003 [−0.003; +0.009] | (M+S) − (M + длина), AUROC, P_ne | [E15_REPORT.md, строка 92](../reports/E15_REPORT.md#L92) |
| [00_front.tex, строка 6](sections/00_front.tex#L6) | [`e15k.dev.s2.ff@L120`](../paper_kit/12_numbers_index.md#L120) | 0.510 [0.459; 0.557] | S (T2), devstral, пары законченных | [E15_REPORT.md, строка 142](../reports/E15_REPORT.md#L142) |
| [00_front.tex, строка 6](sections/00_front.tex#L6) | [`e16.all@L160`](../paper_kit/12_numbers_index.md#L160) | 0.551 [0.520; 0.584] | E16: AUROC C3 внутри единицы, все пары законченных (первичный критерий) | [E16_REPORT.md, строка 53](../reports/E16_REPORT.md#L53) |
| [06_transfer.tex, строка 4](sections/06_transfer.tex#L4) | [`e14.ne.transfer@L57`](../paper_kit/12_numbers_index.md#L57) | 0.631 [0.583; 0.676] | E14: перенос S на Opus 4.5/4.6, P_ne | [E14_REPORT.md, строка 122](../reports/E14_REPORT.md#L122) |
| [06_transfer.tex, строка 4](sections/06_transfer.tex#L4) | [`e14.ne.within@L58`](../paper_kit/12_numbers_index.md#L58) | 0.615 [0.567; 0.662] | E14: S, обучение внутри новых связок, P_ne | [E14_REPORT.md, строка 123](../reports/E14_REPORT.md#L123) |
| [06_transfer.tex, строка 4](sections/06_transfer.tex#L4) | [`e13.ne.v1v2@L45`](../paper_kit/12_numbers_index.md#L45) | 0.613 [0.583; 0.644] | E13: перенос S v1 → v2, AUROC, P_ne | [E13_REPORT.md, строка 223](../reports/E13_REPORT.md#L223) |
| [06_transfer.tex, строка 4](sections/06_transfer.tex#L4) | [`e13.ne.v2v2@L46`](../paper_kit/12_numbers_index.md#L46) | 0.645 [0.614; 0.673] | E13: S обучение и тест внутри v2, AUROC, P_ne | [E13_REPORT.md, строка 224](../reports/E13_REPORT.md#L224) |
| [06_transfer.tex, строка 4](sections/06_transfer.tex#L4) | [`e13.ne.v2v1@L47`](../paper_kit/12_numbers_index.md#L47) | 0.598 [0.574; 0.623] | E13: перенос S v2 → v1, AUROC, P_ne | [E13_REPORT.md, строка 225](../reports/E13_REPORT.md#L225) |
| [06_transfer.tex, строка 4](sections/06_transfer.tex#L4) | [`e13.ne.v1v1@L48`](../paper_kit/12_numbers_index.md#L48) | 0.665 [0.639; 0.689] | E13: S обучение и тест внутри v1, AUROC, P_ne | [E13_REPORT.md, строка 226](../reports/E13_REPORT.md#L226) |

## Проверка

- В аннотации 164 слова при подсчёте по пробелам в печатном тексте, включая числа и границы интервалов. Приведено пять ключевых терминов.
- `python -m src.paper_check --no-fix` подтвердил все 114 вхождений. Расхождений, устаревших строк, неизвестных ключей, чисел без аннотации и отсутствующих ссылок на литературу — 0. Множество из 16 процитированных работ совпадает со списком для статьи из §3 набора.
- `python -m src.paper_build` собрал 5 страниц вместе с литературой. Переполнений, ошибок LaTeX и BibTeX, неразрешённых ссылок и цитат — 0. LaTeX отметил 15 недозаполненных боксов (Underfull).

## Оставшиеся TODO

- [`[TODO: authors]`](sections/00_front.tex#L2)
- [`[TODO: align with the conference AI policy]`](sections/07_related_conclusion.tex#L15)

## Где сила формулировки требует оговорки

- **Перенос внутри формата (VI, Conclusion).** «No measurable loss» — описательное сопоставление на двух отложенных моделях Opus. Эквивалентность не проверялась, нижняя граница интервала не достигла порога протокола.
- **Смена формата (VI, Conclusion).** В обоих направлениях точечный AUROC ниже, чем при обучении в целевом формате. Одновременно меняется набор моделей, поэтому приписать падение одному формату нельзя.
- **Сила модели и описание задачи (VI).** По прямому заданию сюда включены У18 и У19, хотя план набора отводит их диссертации. Связь силы с предсказуемостью не установлена. Вывод об описании задачи относится только к использованному представлению и сравнению с M+S.
- **Сравнение с Mehtiyev и Assunção (VII).** Цитата передаёт вывод аннотации. Разница в уровнях оценки объясняет, почему вопросы различаются. Из аннотации не выводим, как авторы делили данные или какие дополнительные контроли применяли.
- **Самооценка (Abstract, Conclusion).** Общая оценка включает ранее собранные ответы одной модели. Новые пары отдельно слабый сигнал не подтвердили. Вывод о спонтанных заявлениях относится к заявлениям, найденным детектором.
