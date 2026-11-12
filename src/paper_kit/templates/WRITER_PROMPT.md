You are drafting a 6-page IEEE conference paper (English) from the research kit in this
folder. Rules:
1. Use only facts and numbers from this kit. Every number must match 12_numbers_index.md
   exactly, including confidence intervals. Do not round differently, do not invent numbers.
2. Use claims only at the strength given in 01_thesis_and_claims.md ("allowed wording").
   Never use the "forbidden wordings".
3. Cite only works in references.bib. Do not add references.
4. Follow 11_paper_outline.md for structure and page budget; use tables and figures from
   10_figures_tables.md.
5. Position the work against arXiv 2611.08285 and 2610.05572 as in 02_related_work.md:
   concurrent work, what we add.
6. Report negative results plainly. No hype words ("novel", "groundbreaking",
   "significantly" without a test).
7. Output: LaTeX in IEEE conference template, plus a list of every number you used with
   its line in 12_numbers_index.md.
8. Where the kit is silent, write [TODO: …] instead of filling the gap.

Files of the kit (paths relative to this folder):
- 00_README.md — what is in the kit, reading order, rules for the author of the text.
- 01_thesis_and_claims.md — main thesis; claims with status, number, source, allowed and forbidden wordings.
- 02_related_work.md — verified literature; positioning against arXiv 2611.08285 and 2610.05572.
- references.bib — the only references you may cite.
- 03_data.md — data, populations, exclusions, known label noise, what is not in the paper.
- 04_methods.md — task, features S/O/P/M, phase labelling, model, three evaluation levels, metrics, bootstrap,
  pre-registered protocols.
- 05_results_decomposition.md — main leaderboard table (task-difficulty ceiling, M, M + length, S, M+S), precision and
  recall at k.
- 06_results_run_level.md — repeated runs: all pairs vs pairs of finished attempts, best-of-k, variance decomposition.
- 07_results_agent_words.md — spontaneous success claims, elicited self-assessment (E11, E11b, E16).
- 08_transfer_limitations.md — transfer to new models and log formats; benchmark saturation; threats to validity.
- 09_negative_results.md — negative results for the Discussion.
- 10_figures_tables.md — figures (figures/*.pdf, figures/*.png) and tables with captions.
- 11_paper_outline.md — sections, page budget, claims, tables and figures per section.
- 12_numbers_index.md — every number of the kit with its source file and line.
- 13_glossary.md — Russian / English terms.
- CHECK.md — result of the automatic number check.

The kit is in Russian; use the English terms of 13_glossary.md. The source tags "[источник: FILE, стр. N]" in the kit
point to the repository (reports/, outputs/, review/); keep them out of the paper text, use 12_numbers_index.md for
the list of numbers you used.
