# 12. Индекс чисел

Все числа набора. Порядок — по файлу-источнику и строке. Сгенерировано `python -m src.paper_kit.build build` из `src/paper_kit/numbers.py`; проверка — `CHECK.md`.

| ключ | число | что означает | файл-источник | строка |
|---|---|---|---|---|
| `lit.shi.diff` | 0.924 | 2611.08285: сложность задачи по другим моделям, общий AUROC | `docs/LITERATURE.md` | 8 |
| `lit.shi.model_within` | 0.724 | 2611.08285: идентификатор модели, AUROC внутри задачи | `docs/LITERATURE.md` | 8 |
| `lit.shi.prefixes` | 10, 20, 40 | 2611.08285: длины префиксов прогона, шагов | `docs/LITERATURE.md` | 8 |
| `lit.shi.zero` | 18 | 2611.08285: конфигураций, где траектория сверх «задача + модель» ≈ 0 | `docs/LITERATURE.md` | 8 |
| `lit.esf.full` | 0.656 | 2610.05572: поведенческие признаки внутри единицы на полных траекториях | `docs/LITERATURE.md` | 9 |
| `lit.esf.rho` | −0.662 | 2610.05572: сила модели против предсказуемости (LiveClawBench) | `docs/LITERATURE.md` | 9 |
| `lit.ge.786` | 0.786 | Ge et al.: только по описанию задачи | `docs/LITERATURE.md` | 9 |
| `lit.ge.841` | 0.841 | Ge et al. (цитируется в 2610.05572): предсказатель до запуска | `docs/LITERATURE.md` | 9 |
| `lit.vals.top` | 97.00% | Vals AI: лучший результат на SWE-bench Verified, 2026 | `docs/LITERATURE.md` | 38 |
| `e10.k1.both` | +0.014 [+0.002; +0.027] | E10, К1: S+O − S внутри единиц, две настройки | `reports/E10_REPORT.md` | 23 |
| `e10.k4.dev` | +0.82 п.п. [+0.07; +1.61] | E10, К4: Δ(S+O) − Δ(S) при k = 5, devstral | `reports/E10_REPORT.md` | 32 |
| `e10.unknown.dev` | 16.5% | E10: доля проверок unknown у devstral-2512 | `reports/E10_REPORT.md` | 56 |
| `e10.o19` | 19 признаков | E10: основной набор O | `reports/E10_REPORT.md` | 73 |
| `e10.sel.dev.so` | +1.58 п.п. [+0.50; +2.66] | E10: выбор из 5 по S+O, devstral, к случайной среди отправивших | `reports/E10_REPORT.md` | 133 |
| `e11.pairs` | 100 | E11: пар (решённая и нерешённая попытка одной задачи) | `reports/E11_REPORT.md` | 6 |
| `e11.c4.ff` | 0.500 [0.431; 0.569] | E11: AUROC C4 на парах законченных | `reports/E11_REPORT.md` | 22 |
| `e11.hfm` | 0.565 [0.505; 0.630] | E11: AUROC признака «есть финальное сообщение» | `reports/E11_REPORT.md` | 23 |
| `e11.c4.all` | 0.590 [0.540; 0.645] | E11: AUROC C4 на всех попытках | `reports/E11_REPORT.md` | 64 |
| `e11.conf` | 96.5 против 96.6 | E11: средняя уверенность нерешённых и решённых при пройденной последней проверке | `reports/E11_REPORT.md` | 161 |
| `e11.knows` | 5 из 32 | E11: «знает, но молчит» среди нерешённых с упавшей проверкой | `reports/E11_REPORT.md` | 180 |
| `e11b.c4` | 0.518 [0.469; 0.562] | E11b: AUROC C4 («Be honest») | `reports/E11b_REPORT.md` | 15 |
| `e11b.c4_c3` | −0.054 [−0.103; +0.000] | E11b: C4 − C3 | `reports/E11b_REPORT.md` | 16 |
| `e11b.c3` | 0.571 [0.518; 0.625] | E11b: AUROC C3 (описательно) | `reports/E11b_REPORT.md` | 45 |
| `e11b.pairs` | 112 | E11b: пар законченных попыток | `reports/E11b_REPORT.md` | 77 |
| `e12.diff` | −0.015 [−0.044; +0.013] | E12: (T2+M+S) − (M+S), AUROC | `reports/E12_REPORT.md` | 10 |
| `e12.t2` | 0.555 [0.514; 0.596] | E12: эмбеддинг описания задачи, AUROC | `reports/E12_REPORT.md` | 32 |
| `e13.P` | +0.0115 [+0.0022; +0.0212] | E13: (M+S+P) − (M+S), AUROC | `reports/E13_REPORT.md` | 11 |
| `e13.rho` | −0.24 [−0.70; +0.32] | E13: ρ Спирмена «сила модели — AUROC S», общая модель, 16 связок | `reports/E13_REPORT.md` | 13 |
| `e13.v1v2` | 0.596 [0.567; 0.626] | E13: перенос S v1 → v2, AUROC | `reports/E13_REPORT.md` | 35 |
| `e13.v2v2` | 0.645 [0.616; 0.674] | E13: S обучение и тест внутри v2, AUROC | `reports/E13_REPORT.md` | 37 |
| `e13.v2v1` | 0.563 [0.538; 0.586] | E13: перенос S v2 → v1, AUROC | `reports/E13_REPORT.md` | 45 |
| `e13.P.within` | −0.0048 [−0.0168; +0.0071] | E13: (M+S+P) − (M+S), внутри задачи | `reports/E13_REPORT.md` | 161 |
| `e14.thr` | 0.6 | E14: порог критерия (нижняя граница AUROC) | `reports/E14_REPORT.md` | 10 |
| `e14.runs` | 985 | E14: прогонов двух связок Opus | `reports/E14_REPORT.md` | 31 |
| `e14.transfer` | 0.636 [0.587; 0.681] | E14: перенос S на Opus 4.5/4.6, AUROC | `reports/E14_REPORT.md` | 41 |
| `e14.within` | 0.617 [0.570; 0.664] | E14: S, обучение внутри новых связок, AUROC | `reports/E14_REPORT.md` | 43 |
| `e14.opus45` | 0.662 [0.602; 0.720] | E14: перенос S, Opus 4.5 | `reports/E14_REPORT.md` | 56 |
| `e14.opus46` | 0.635 [0.580; 0.692] | E14: перенос S, Opus 4.6 | `reports/E14_REPORT.md` | 57 |
| `e15v.disc.dev` | 0 расхождений из 2 609 пар | одинаковые патчи с разной меткой, devstral-2512 | `reports/E15_REPORT.md` | 35 |
| `e15v.disc.qwen` | 4 из 2 276 | одинаковые патчи с разной меткой, Qwen3-32B | `reports/E15_REPORT.md` | 35 |
| `e15.ne.base` | 0.319 | доля нерешённых в P_ne | `reports/E15_REPORT.md` | 44 |
| `e15.ne.runs` | 7 732 | прогонов в P_ne (лидерборд без пустых патчей) | `reports/E15_REPORT.md` | 44 |
| `e15.ci` | 95%, кластерный бутстрэп по задачам | интервалы E15, часть 1 | `reports/E15_REPORT.md` | 46 |
| `e15.ne.D` | 0.926 [0.912; 0.936] | AUROC: сложность задачи по другим моделям (потолок), P_ne | `reports/E15_REPORT.md` | 51 |
| `e15.ne.D.p10` | 0.972 [0.957; 0.983] | precision@10%: сложность задачи (потолок), P_ne | `reports/E15_REPORT.md` | 51 |
| `e15.ne.D.rec` | 0.305 | recall@10%: сложность задачи (потолок), P_ne | `reports/E15_REPORT.md` | 51 |
| `e15.ne.M` | 0.584 [0.572; 0.599] | AUROC модели M, P_ne | `reports/E15_REPORT.md` | 52 |
| `e15.ne.M.p10` | 0.445 [0.410; 0.488] | precision@10%, M, P_ne | `reports/E15_REPORT.md` | 52 |
| `e15.ne.M.rec` | 0.140 | recall@10%, M, P_ne | `reports/E15_REPORT.md` | 52 |
| `e15.ne.M.within` | 0.734 [0.707; 0.763] | AUROC внутри задачи, M, P_ne | `reports/E15_REPORT.md` | 52 |
| `e15.ne.MN` | 0.673 [0.649; 0.698] | AUROC M + log шагов, P_ne | `reports/E15_REPORT.md` | 53 |
| `e15.ne.MN.p10` | 0.578 [0.522; 0.633] | precision@10%, M + log шагов, P_ne | `reports/E15_REPORT.md` | 53 |
| `e15.ne.MN.rec` | 0.181 | recall@10%, M + log шагов, P_ne | `reports/E15_REPORT.md` | 53 |
| `e15.ne.MN.within` | 0.661 [0.636; 0.687] | внутри задачи, M + log шагов, P_ne | `reports/E15_REPORT.md` | 53 |
| `e15.ne.MLEN` | 0.675 [0.652; 0.697] | AUROC M + длина, P_ne | `reports/E15_REPORT.md` | 54 |
| `e15.ne.MLEN.p10` | 0.605 [0.545; 0.659] | precision@10%, M + длина, P_ne | `reports/E15_REPORT.md` | 54 |
| `e15.ne.MLEN.p20` | 0.531 | precision@20%, M + длина, P_ne | `reports/E15_REPORT.md` | 54 |
| `e15.ne.MLEN.p5` | 0.646 | precision@5%, M + длина, P_ne | `reports/E15_REPORT.md` | 54 |
| `e15.ne.MLEN.rec` | 0.190 | recall@10%, M + длина, P_ne | `reports/E15_REPORT.md` | 54 |
| `e15.ne.MLEN.within` | 0.667 [0.641; 0.693] | внутри задачи, M + длина, P_ne | `reports/E15_REPORT.md` | 54 |
| `e15.ne.S` | 0.654 [0.630; 0.676] | AUROC S, P_ne | `reports/E15_REPORT.md` | 55 |
| `e15.ne.S.p10` | 0.561 [0.506; 0.614] | precision@10%, S, P_ne | `reports/E15_REPORT.md` | 55 |
| `e15.ne.S.rec` | 0.176 | recall@10%, S, P_ne | `reports/E15_REPORT.md` | 55 |
| `e15.ne.S.within` | 0.602 [0.576; 0.629] | внутри задачи, S, P_ne | `reports/E15_REPORT.md` | 55 |
| `e15.ne.MS` | 0.678 [0.653; 0.701] | AUROC M+S, P_ne | `reports/E15_REPORT.md` | 56 |
| `e15.ne.MS.p10` | 0.603 [0.542; 0.658] | precision@10%, M+S, P_ne | `reports/E15_REPORT.md` | 56 |
| `e15.ne.MS.p20` | 0.536 | precision@20%, M+S, P_ne | `reports/E15_REPORT.md` | 56 |
| `e15.ne.MS.p5` | 0.638 | precision@5%, M+S, P_ne | `reports/E15_REPORT.md` | 56 |
| `e15.ne.MS.rec` | 0.189 | recall@10%, M+S, P_ne | `reports/E15_REPORT.md` | 56 |
| `e15.ne.MS.within` | 0.658 [0.632; 0.685] | внутри задачи, M+S, P_ne | `reports/E15_REPORT.md` | 56 |
| `e15.ne.DMS` | 0.945 [0.934; 0.953] | AUROC D+M+S (потолок), P_ne | `reports/E15_REPORT.md` | 58 |
| `e15.ne.maxrec` | 0.313 | наибольший возможный recall@10% в P_ne | `reports/E15_REPORT.md` | 61 |
| `e15.ne.MNgbm` | 0.658 | AUROC M + log шагов бустингом, P_ne | `reports/E15_REPORT.md` | 63 |
| `e15.all.base` | 0.330 | доля нерешённых в P_all | `reports/E15_REPORT.md` | 66 |
| `e15.all.runs` | 7 864 | прогонов в P_all (популяция E7) | `reports/E15_REPORT.md` | 66 |
| `e15.all.MS` | 0.685 [0.662; 0.706] | AUROC M+S, P_all | `reports/E15_REPORT.md` | 77 |
| `e15.all.MS.p10` | 0.656 [0.604; 0.706] | precision@10% M+S, P_all | `reports/E15_REPORT.md` | 77 |
| `e15.all.emptyfirst` | 0.677 [0.625; 0.726] | precision@10% правила «пустой патч первым», P_all | `reports/E15_REPORT.md` | 79 |
| `e15.empty.n` | 132 | пустых отправленных патчей | `reports/E15_REPORT.md` | 81 |
| `e15.empty.top10` | 98 | пустых патчей в верхних 10% по M+S | `reports/E15_REPORT.md` | 81 |
| `e15.lift.all` | 1.99 | лифт precision@10% M+S, P_all | `reports/E15_REPORT.md` | 84 |
| `e15.lift.ne` | 1.89 | лифт precision@10% M+S, P_ne | `reports/E15_REPORT.md` | 84 |
| `e15.diff.MS_MN.ne` | +0.005 [−0.003; +0.012] | (M+S) − (M + log шагов), AUROC, P_ne | `reports/E15_REPORT.md` | 90 |
| `e15.diff.MS_MLEN.ne` | +0.003 [−0.003; +0.009] | (M+S) − (M + длина), AUROC, P_ne | `reports/E15_REPORT.md` | 92 |
| `e15.diff.MS_M.within.ne` | −0.077 [−0.098; −0.057] | (M+S) − M внутри задачи, P_ne | `reports/E15_REPORT.md` | 94 |
| `e15.diff.DMS_DM.ne` | +0.001 [−0.000; +0.002] | (D+M+S) − (D+M), AUROC, P_ne | `reports/E15_REPORT.md` | 96 |
| `e15.diff.DMS_DM.all` | +0.008 [+0.005; +0.011] | (D+M+S) − (D+M), AUROC, P_all | `reports/E15_REPORT.md` | 97 |
| `e15k.dev.fin_share` | 0.806 | доля законченных попыток, devstral-2512 | `reports/E15_REPORT.md` | 125 |
| `e15k.dev.unres_fin` | 0.304 / 0.599 | не решено среди законченных / оборванных, devstral-2512 | `reports/E15_REPORT.md` | 126 |
| `e15k.dev.pairs.all` | 2 920 | пар внутри единиц, devstral-2512, все | `reports/E15_REPORT.md` | 128 |
| `e15k.dev.pairs.ff` | 1 493 | пар законченных попыток, devstral-2512 | `reports/E15_REPORT.md` | 128 |
| `e15k.dev.nf.all` | 0.544 [0.518; 0.570] | AUROC внутри единицы базы «не закончена», devstral, все пары | `reports/E15_REPORT.md` | 139 |
| `e15k.dev.s1.all` | 0.581 [0.545; 0.618] | S (T1, две настройки вместе), devstral, все пары | `reports/E15_REPORT.md` | 141 |
| `e15k.dev.s1.ff` | 0.539 [0.495; 0.582] | S (T1), devstral, пары законченных | `reports/E15_REPORT.md` | 141 |
| `e15k.dev.s2.all` | 0.561 [0.522; 0.598] | S (T2, внутри настройки), devstral, все пары | `reports/E15_REPORT.md` | 142 |
| `e15k.dev.s2.ff` | 0.510 [0.459; 0.557] | S (T2), devstral, пары законченных | `reports/E15_REPORT.md` | 142 |
| `e15k.dev.so1.ff` | 0.559 [0.511; 0.605] | S+O (T1), devstral, пары законченных | `reports/E15_REPORT.md` | 146 |
| `e15k.dev.so2.all` | 0.585 [0.548; 0.620] | S+O (T2), devstral, все пары | `reports/E15_REPORT.md` | 147 |
| `e15k.dev.so2.ff` | 0.549 [0.502; 0.593] | S+O (T2), devstral, пары законченных | `reports/E15_REPORT.md` | 147 |
| `e15k.dev.s1_nf` | +0.037 [−0.000; +0.076] | S (T1) − «не закончена», devstral, все пары | `reports/E15_REPORT.md` | 152 |
| `e15k.dev.s2_nf` | +0.017 [−0.021; +0.057] | S (T2) − «не закончена», devstral, все пары | `reports/E15_REPORT.md` | 153 |
| `e15k.dev.so_s_ff` | +0.038 [+0.011; +0.069] | S+O − S (T2), devstral, пары законченных | `reports/E15_REPORT.md` | 154 |
| `e15k.both.s1.ff` | 0.534 [0.503; 0.563] | S (T1), две настройки вместе, пары законченных | `reports/E15_REPORT.md` | 162 |
| `e15k.qwen.s1.ff` | 0.531 [0.491; 0.571] | S (T1), Qwen3-32B, пары законченных | `reports/E15_REPORT.md` | 162 |
| `e15k.both.s2.ff` | 0.503 [0.471; 0.533] | S (T2), две настройки вместе, пары законченных | `reports/E15_REPORT.md` | 163 |
| `e15k.qwen.s2.ff` | 0.498 [0.456; 0.539] | S (T2), Qwen3-32B, пары законченных | `reports/E15_REPORT.md` | 163 |
| `e15k.k` | k = 5 | главное k выбора из k | `reports/E15_REPORT.md` | 187 |
| `e15k.sel.dev.fin` | +0.95 [+0.37; +1.55] | выбор из 5: случайная среди законченных − случайная среди отправивших, devstral, п.п. | `reports/E15_REPORT.md` | 194 |
| `e15k.sel.dev.s1fin` | +0.71 [−0.18; +1.68] | выбор из 5: S (T1) среди законченных − случайная среди законченных, devstral, п.п. | `reports/E15_REPORT.md` | 199 |
| `e15k.sel.dev.s2fin` | +0.02 [−0.94; +0.88] | выбор из 5: S (T2) среди законченных − случайная среди законченных, devstral, п.п. | `reports/E15_REPORT.md` | 200 |
| `e15v.lat.model` | 28.4% [26.0; 31.6] | латентная доля модели (настройки) | `reports/E15_REPORT.md` | 237 |
| `e15v.lat.task` | 50.0% [43.9; 54.7] | латентная доля задачи (популяция E8, две настройки) | `reports/E15_REPORT.md` | 238 |
| `e15v.lat.task.pt` | 50.0% | латентная доля задачи (точечно) | `reports/E15_REPORT.md` | 238 |
| `e15v.lat.txm` | 12.9% [8.8; 16.5] | латентная доля задача × модель | `reports/E15_REPORT.md` | 239 |
| `e15v.lat.attempt` | 8.6% [7.3; 10.0] | латентная доля попытки | `reports/E15_REPORT.md` | 240 |
| `e15v.lat.attempt.pt` | 8.6% | латентная доля попытки (точечно) | `reports/E15_REPORT.md` | 240 |
| `e15v.obs.attempt` | 26.0% [24.0; 28.3] | доля попытки на наблюдаемой шкале | `reports/E15_REPORT.md` | 240 |
| `e15v.ceil.dev` | 1.000 | потолок AUROC внутри единицы при оценённом шуме, devstral-2512 | `reports/E15_REPORT.md` | 262 |
| `e15v.ceil.qwen` | 0.992 | потолок AUROC внутри единицы при оценённом шуме, Qwen3-32B | `reports/E15_REPORT.md` | 262 |
| `e15v.thr` | 0.9 | порог правила чтения 3.7 (потолок выше — исход определяется попыткой) | `reports/E15_REPORT.md` | 293 |
| `e16.mostly` | 95 или 100 | E16: типичные ответы C3 | `reports/E16_REPORT.md` | 21 |
| `e16.share100.fail` | 49.4% | E16: доля ответа «100» у нерешённых | `reports/E16_REPORT.md` | 22 |
| `e16.share100.ok` | 62.2% | E16: доля ответа «100» у решённых | `reports/E16_REPORT.md` | 22 |
| `e16.cost` | $8.357 | E16: расход на запросы | `reports/E16_REPORT.md` | 34 |
| `e16.all` | 0.551 [0.520; 0.584] | E16: AUROC C3 внутри единицы, все пары законченных (первичный критерий) | `reports/E16_REPORT.md` | 53 |
| `e16.pairs.all` | 1 475 | E16: пар законченных попыток | `reports/E16_REPORT.md` | 53 |
| `e16.new` | 0.534 [0.489; 0.581] | E16: то же, только новые ответы (вторичный критерий) | `reports/E16_REPORT.md` | 54 |
| `e16.pairs.new` | 595 | E16: пар только из новых ответов | `reports/E16_REPORT.md` | 54 |
| `e16.c3_so` | +0.005 [−0.045; +0.055] | E16: C3 − S+O, все пары | `reports/E16_REPORT.md` | 67 |
| `e16.c3_g0` | +0.014 [−0.028; +0.055] | E16: C3 − G0, все пары | `reports/E16_REPORT.md` | 68 |
| `e16.pt.mixed` | 0.561 | E16, вне протокола: C3, один старый и один новый ответ | `reports/E16_REPORT.md` | 112 |
| `e16.pt.new` | 0.534 | E16, вне протокола: C3, оба ответа новые | `reports/E16_REPORT.md` | 112 |
| `e16.pt.old` | 0.571 | E16, вне протокола: C3, оба ответа из E11b | `reports/E16_REPORT.md` | 112 |
| `e4.delta` | 0.014 [95% ДИ -0.004; 0.031] | итерация 1: прирост AUROC хаотичности над длиной | `reports/E4_results.md` | 10 |
| `e4.bh` | 3 | итерация 1: конфигураций, значимых после поправки BH | `reports/E4_results.md` | 137 |
| `e7.T` | 0.515 [0.472; 0.557] | E7: TF-IDF описания задачи, AUROC | `reports/E7_REPORT.md` | 29 |
| `e7.A` | +0.009 [+0.004; +0.015] | E7: финальное сообщение (A) сверх T+M+S, AUROC | `reports/E7_REPORT.md` | 68 |
| `e7.R` | +0.018 [+0.007; +0.029] | E7: рассуждения (R) сверх T+M+S, AUROC | `reports/E7_REPORT.md` | 74 |
| `kth.runs_per_task` | 10 | прогонов на задачу в повторах KTH | `reports/E8_REPORT.md` | 9 |
| `e8.boot` | 1000 выборок, seed 42 | бутстрэп (число выборок, seed) | `reports/E8_REPORT.md` | 14 |
| `e8.dev.holm` | 0.002 | E8: p после поправки Холма, devstral-2512 | `reports/E8_REPORT.md` | 20 |
| `e8.dev.p2` | 0.581 [0.545; 0.618] | E8: P2 внутри единиц, devstral-2512 | `reports/E8_REPORT.md` | 20 |
| `e8.both.p2` | 0.559 [0.531; 0.589] | E8: P2 внутри единиц, обе настройки | `reports/E8_REPORT.md` | 21 |
| `e8.qwen.holm` | 0.060 | E8: p после поправки Холма, Qwen3-32B | `reports/E8_REPORT.md` | 23 |
| `e8.qwen.p2` | 0.530 [0.489; 0.571] | E8: P2 внутри единиц, Qwen3-32B | `reports/E8_REPORT.md` | 23 |
| `e8.thr` | 0.60 | E8: порог практической значимости | `reports/E8_REPORT.md` | 24 |
| `e8.both.runs` | 9225 | попыток с меткой, обе | `reports/E8_REPORT.md` | 36 |
| `e8.dev.runs` | 4978 | попыток с меткой, devstral-2512 | `reports/E8_REPORT.md` | 36 |
| `e8.qwen.runs` | 4247 | попыток с меткой, Qwen3-32B | `reports/E8_REPORT.md` | 36 |
| `e8.both.unres` | 56.7% | доля нерешённых, обе | `reports/E8_REPORT.md` | 38 |
| `e8.dev.unres` | 36.2% | доля нерешённых, devstral-2512 | `reports/E8_REPORT.md` | 38 |
| `e8.qwen.unres` | 80.7% | доля нерешённых, Qwen3-32B | `reports/E8_REPORT.md` | 38 |
| `e8.both.pairs` | 5096 | пар внутри единиц, обе | `reports/E8_REPORT.md` | 41 |
| `e8.dev.pairs` | 2920 | пар внутри единиц, devstral-2512 | `reports/E8_REPORT.md` | 41 |
| `e8.qwen.pairs` | 2176 | пар внутри единиц, Qwen3-32B | `reports/E8_REPORT.md` | 41 |
| `e8.both.steps` | 37 | медиана шагов агента, обе | `reports/E8_REPORT.md` | 43 |
| `e8.dev.steps` | 66 | медиана шагов агента, devstral-2512 | `reports/E8_REPORT.md` | 43 |
| `e8.qwen.steps` | 7 | медиана шагов агента, Qwen3-32B | `reports/E8_REPORT.md` | 43 |
| `e8.both.verif` | 50.6% | доля прогонов с запуском тестов, обе | `reports/E8_REPORT.md` | 44 |
| `e8.dev.verif` | 93.3% | доля прогонов с запуском тестов, devstral-2512 | `reports/E8_REPORT.md` | 44 |
| `e8.qwen.verif` | 0.6% | доля прогонов с запуском тестов, Qwen3-32B | `reports/E8_REPORT.md` | 44 |
| `e8.both.fin` | 88.8% | доля законченных финальным сообщением, обе | `reports/E8_REPORT.md` | 45 |
| `e8.dev.fin` | 80.6% | доля законченных финальным сообщением, devstral-2512 | `reports/E8_REPORT.md` | 45 |
| `e8.qwen.fin` | 98.4% | доля законченных финальным сообщением, Qwen3-32B | `reports/E8_REPORT.md` | 45 |
| `e9.nsub` | 200 | E9: случайных подвыборок по k попыток на задачу | `reports/E9_PROTOCOL.md` | 67 |
| `e9.dev.p2` | +0.77 п.п. [−0.31; +1.85] | E9: выбор из 5 по S (T2), devstral, к случайной среди отправивших | `reports/E9_REPORT.md` | 17 |
| `e9.both` | +0.08 [−0.68; +0.83] | E9: то же, обе настройки вместе, п.п. | `reports/E9_REPORT.md` | 19 |
| `e9.thr` | +2 | E9: порог практического выигрыша, п.п. | `reports/E9_REPORT.md` | 20 |
| `g0.hc.n` | 100 | G0: проверено финальных сообщений | `reports/G0_HUMAN_CHECK.md` | 14 |
| `g0.det.prec` | 0.96–1.00 | G0: точность детектора | `reports/G0_HUMAN_CHECK.md` | 24 |
| `g0.hc.strict` | −0.157 [−0.321; +0.017] | G0 после поправки: разность долей, строгий вариант | `reports/G0_HUMAN_CHECK.md` | 34 |
| `g0.hc.strict.fail` | 0.679 [0.556; 0.803] | G0 после поправки: доля явных заявлений у нерешённых | `reports/G0_HUMAN_CHECK.md` | 34 |
| `g0.hc.strict.ok` | 0.522 [0.417; 0.651] | G0 после поправки: доля явных заявлений у решённых | `reports/G0_HUMAN_CHECK.md` | 34 |
| `g0.hc.broad` | −0.057 [−0.208; +0.094] | G0 после поправки: разность долей, широкий вариант | `reports/G0_HUMAN_CHECK.md` | 35 |
| `g0.hc.broad.fail` | 0.877 [0.778; 0.951] | G0 после поправки: то же у нерешённых | `reports/G0_HUMAN_CHECK.md` | 35 |
| `g0.hc.broad.ok` | 0.820 [0.717; 0.923] | G0 после поправки: доля заявлений (широкий вариант) у решённых | `reports/G0_HUMAN_CHECK.md` | 35 |
| `g0.det.rec.fail` | 0.564 [0.478; 0.689] | G0: полнота детектора, строгий вариант, нерешённые | `reports/G0_HUMAN_CHECK.md` | 49 |
| `g0.det.rec.ok` | 0.655 [0.526; 0.816] | G0: полнота детектора, строгий вариант, решённые | `reports/G0_HUMAN_CHECK.md` | 49 |
| `g0.claim.fail` | 36.9% | G0, детектор: заявление успеха у провальных | `reports/G0_false_success.md` | 28 |
| `g0.claim.ok` | 35.6% | G0, детектор: заявление успеха у успешных | `reports/G0_false_success.md` | 28 |
| `deep.empty_labelled` | 968 | DeepSWE-Preview: попыток без патча с меткой | `reports/KTH_DEEPSWE_LABELS.md` | 49 |
| `deep.empty_resolved` | 193 | DeepSWE-Preview: из них «решено» | `reports/KTH_DEEPSWE_LABELS.md` | 49 |
| `deep.disagree.qwen` | 0.002 | Qwen3-32B: то же | `reports/KTH_DEEPSWE_LABELS.md` | 59 |
| `deep.disagree.dev` | 0.000 | devstral-2512: то же | `reports/KTH_DEEPSWE_LABELS.md` | 60 |
| `deep.disagree` | 0.185 | DeepSWE-Preview: доля пар одинаковых патчей с разной меткой | `reports/KTH_DEEPSWE_LABELS.md` | 61 |
| `g0.simpson.all` | −2.7 п.п. [−5.2; −0.3] | G0, детектор: разность долей заявлений (решённые − нерешённые) в целом | `review/REVIEW.md` | 38 |
| `g0.simpson.within` | +2.7 п.п. [+0.6; +4.9] | G0, детектор: та же разность внутри связки | `review/REVIEW.md` | 38 |
| `rev.g0.power` | 24 п.п. | G0: наименьшая разность, которую видит проверка по 100 сообщениям | `review/REVIEW.md` | 39 |
| `rev.s20` | 20 | разных признаков S | `review/REVIEW.md` | 248 |
| `rev.s21` | 21 | столбцов признаков S | `review/REVIEW.md` | 248 |
| `rev.combo.mean` | 0.646 | рецензия: средний AUROC S одной связки корпуса | `review/REVIEW.md` | 318 |
| `rev.rho.within` | −0.61 | рецензия: ρ «сила — AUROC», модель внутри связки | `review/REVIEW.md` | 335 |
| `rev.rho.within.ne` | −0.50 | рецензия: то же без пустых патчей | `review/REVIEW.md` | 336 |
| `rev.gbm.ms` | 0.672 | рецензия: бустинг M+S, AUROC | `review/REVIEW.md` | 479 |
| `rev.combo.range` | 0.56–0.73 | рецензия: разброс AUROC S по связкам | `review/REVIEW.md` | 481 |
| `rev.pages` | 6 | рецензия: объём статьи, страниц | `review/REVIEW.md` | 544 |
| `rev.p1` | 0.6 | рецензия: объём раздела 1. Введение, страниц | `review/REVIEW.md` | 550 |
| `rev.p2` | 0.9 | рецензия: объём раздела 2. Данные и протокол, страниц | `review/REVIEW.md` | 551 |
| `rev.p3` | 1.3 | рецензия: объём раздела 3. Разложение, страниц | `review/REVIEW.md` | 552 |
| `rev.p4` | 1.3 | рецензия: объём раздела 4. Уровень попытки, страниц | `review/REVIEW.md` | 553 |
| `rev.p5` | 1.0 | рецензия: объём раздела 5. Слова агента, страниц | `review/REVIEW.md` | 554 |
| `rev.p6` | 0.6 | рецензия: объём раздела 6. Перенос и ограничения, страниц | `review/REVIEW.md` | 555 |
| `rev.p7` | 0.3 | рецензия: объём раздела 7. Связанные работы и вывод, страниц | `review/REVIEW.md` | 556 |
