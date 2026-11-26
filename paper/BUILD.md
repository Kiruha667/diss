# Сборка статьи

Сгенерировано `python -m src.paper_build` 2026-11-15: `latexmk -gg -pdf` в `paper/` (полная сборка с нуля). Инструменты: pdfTeX 3.141592653-2.6-1.40.26 (TeX Live 2025/dev/Debian); latexmk John Collins, 11 Dec. 2024. Version 4.86. PDF (`paper/main.pdf`) в git не добавляется.

**Итог: собрано, замечаний нет.**

- Код выхода latexmk: 0.
- Страниц: 3 (220 КБ); объём по плану — 6.0 с. вместе с заголовком, аннотацией и литературой (`paper_kit/11_paper_outline.md`).
- Переполнения боксов (Overfull): 0; недозаполнения (Underfull): 7.
- Неразрешённые ссылки (\ref): 0; цитаты (\cite): 0; нужен повторный проход: нет.
- BibTeX: ошибок 0, предупреждений 0.
- Ошибки LaTeX: 0; прочие предупреждения LaTeX и пакетов: 0.

## Underfull

- `sections/03_decomposition.tex`: \hbox (badness 10000) in paragraph at lines 30--31
- `sections/03_decomposition.tex`: \hbox (badness 6842) in paragraph at lines 30--31
- `sections/03_decomposition.tex`: \hbox (badness 10000) in paragraph at lines 30--31
- `sections/04_attempt_level.tex`: \hbox (badness 2529) in paragraph at lines 8--9
- `sections/04_attempt_level.tex`: \hbox (badness 1910) in paragraph at lines 8--9
- `sections/04_attempt_level.tex`: \hbox (badness 2253) in paragraph at lines 8--9
- `main.bbl`: \hbox (badness 3189) in paragraph at lines 30--32

