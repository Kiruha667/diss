# Сборка статьи

Сгенерировано `python -m src.paper_build` 2026-11-15: `latexmk -gg -pdf` в `paper/` (полная сборка с нуля). Инструменты: pdfTeX 3.141592653-2.6-1.40.29 (TeX Live 2026); latexmk John Collins, 8 November 2026. Version 4.89. PDF (`paper/main.pdf`) в git не добавляется.

**Итог: собрано, замечаний нет.**

- Код выхода latexmk: 0.
- Страниц: 2 (110 КБ); объём по плану — 6.0 с. вместе с заголовком, аннотацией и литературой (`paper_kit/11_paper_outline.md`).
- Переполнения боксов (Overfull): 0; недозаполнения (Underfull): 1.
- Неразрешённые ссылки (\ref): 0; цитаты (\cite): 0; нужен повторный проход: нет.
- BibTeX: ошибок 0, предупреждений 0.
- Ошибки LaTeX: 0; прочие предупреждения LaTeX и пакетов: 0.

## Underfull

- `sections/02_data.tex`: \hbox (badness 4765) in paragraph at lines 29--30

