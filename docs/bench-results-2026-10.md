# Bench results, October 2026 (M6 calibration run)

The M6 calibration run (Jira CGL-7) measured two open models as Coglossa's bundled-model
candidates, compared the pipeline with a single-model baseline, and calibrated the flag
thresholds (the language tiers were checked and kept). It ran on 2026-10-13 and 14. The configs and outputs are
in `bench/m6-calibration/` (outputs under `results/`); how to repeat it is at the top of its
`bench.yaml`.

## In short

- **Gemma 4 26B-A4B is the better bundled model.** Out of English it beat Qwen3.6-35B-A3B on
  almost every pair, and by far on low-resource targets (Hausa 3.4 against 5.1–6.1 MetricX,
  Swahili 2.3 against 3.5–3.8, Khmer 2.4 against 3.5–4.3); into English the two were even. It
  finished every pair. Qwen3.6 fell into repetition
  loops on low-resource targets and never finished Amharic, alone or in the pipeline.
- **On single FLORES sentences the pipeline translates no better than one model in one pass.**
  The mean MetricX difference is 0.00 for Qwen and −0.02 for Gemma (lower is better), at 7–12
  times the tokens and 3–8 times the time. FLORES sentences are short and unrelated, so the
  things the pipeline adds for documents (the translation guide, consistent names and terms
  across chunks, reconciling two translations of a long passage) have nothing to work on. What
  the run does show is that the pipeline is more robust: it splits and retries a chunk a model
  cannot finish, where the baseline loses the chunk.
- **The run found and fixed five product bugs** (below), one of which sent a model into the
  wrong language.
- **The flags are calibrated.** The new default thresholds flag about one correct sentence in
  ten in each tier and catch 86% of injected meaning errors in tier A, 84% in tier B and 68% in
  tier C. Changed numbers were the weak spot (29–49% caught); the numbers check added since
  (CGL-86, below) catches 91–99% of them in an offline rerun of the check alone (no models).

## Setup

| | |
|---|---|
| Data | FLORES-200 devtest, 100 sentences per pair (seed 1) |
| Pairs | 18 out of English (es, zh-Hans, ru, fr, de, ja, ar, hi, tr, vi, uk, id, sw, yo, am, ne, km, ha) and 3 into English (es, ar, sw) |
| Models | `qwen/qwen3.6-35b-a3b` and `google/gemma-4-26b-a4b-it` on OpenRouter, both Apache-2.0 |
| Systems | `qwen-pipeline` (Qwen translates, reconciles and builds the guide; Gemma judges and back-translates), `gemma-pipeline` (the mirror), and each model alone as a one-pass baseline |
| Metrics | MetricX-24 hybrid large (lower is better, 0–25) and chrF (higher is better), from a CPU scorer |
| Cost | about $12.50 of OpenRouter credit, including runs repeated after the fixes below |

Both models ran at Coglossa's default temperature (0.2) with a 16,384-token output limit and a
65,536-token context window. Qwen3.6 reasons before it answers unless told not to, which cost
about 40 times the tokens on a short call (339 against 8), so it ran with
`extra_body: {reasoning: {enabled: false}}`. OpenRouter's provider routing was pinned to
CoreWeave, DeepInfra and Parasail at bf16 or fp8 after one call to another provider took 1,362
seconds. The pinning changes where the same model runs, not the model, so Qwen pipeline results
finished before it were kept (their saved fingerprint was updated to the pinned config).

Every pair that failed was run again (the Qwen baseline pairs twice more); a pair that never
finished is reported as failed. A first full run, made before the language-name fix below, is
not reported: every result was made again after it.

## Translation quality

MetricX / chrF per pair. MetricX is over the sentences every finished system of the pair scored,
so the four columns of a row are comparable.

| pair | qwen pipeline | qwen baseline | gemma pipeline | gemma baseline |
|---|---|---|---|---|
| en>am | failed | failed | 2.38 / 40.8 | 2.74 / 39.5 |
| en>ar | 1.82 / 57.8 | 1.91 / 58.1 | 1.49 / 58.9 | 1.55 / 58.0 |
| en>de | 0.79 / 67.1 | 0.84 / 67.3 | 0.79 / 66.2 | 0.70 / 66.8 |
| en>es | 1.82 / 55.2 | 1.81 / 55.6 | 1.49 / 56.0 | 1.52 / 56.0 |
| en>fr | 1.54 / 70.5 | 1.54 / 70.1 | 1.51 / 70.9 | 1.49 / 69.9 |
| en>ha | 6.10 / 45.3 | 5.14 / 46.4 | 3.41 / 49.7 | 3.42 / 49.7 |
| en>hi | 3.08 / 54.8 | 2.96 / 54.7 | 2.32 / 59.7 | 2.56 / 58.2 |
| en>id | 1.46 / 70.5 | 1.44 / 70.4 | 1.28 / 71.6 | 1.26 / 71.9 |
| en>ja | 1.93 / 41.2 | 2.08 / 41.0 | 1.68 / 40.7 | 1.65 / 41.7 |
| en>km | 3.52 / 42.5 | 4.27 / 41.9 | 2.41 / 48.0 | 2.38 / 48.0 |
| en>ne | 2.58 / 52.4 | 2.82 / 54.2 | 1.99 / 54.8 | 2.16 / 52.3 |
| en>ru | 1.39 / 58.4 | 1.39 / 58.5 | 1.43 / 60.1 | 1.32 / 59.2 |
| en>sw | 3.51 / 58.7 | 3.80 / 59.0 | 2.27 / 62.2 | 2.29 / 63.0 |
| en>tr | 3.13 / 59.5 | 2.88 / 60.1 | 2.19 / 63.3 | 2.09 / 63.7 |
| en>uk | 1.69 / 58.5 | 1.75 / 59.0 | 1.69 / 59.1 | 1.76 / 58.9 |
| en>vi | 1.81 / 62.0 | 1.80 / 62.2 | 1.65 / 62.8 | 1.60 / 63.0 |
| en>yo | 6.98 / 22.8 | failed | 7.48 / 29.2 | 7.40 / 29.4 |
| en>zh-Hans | 1.49 / 40.1 | 1.41 / 40.4 | 1.29 / 40.6 | 1.34 / 40.7 |
| ar>en | 1.99 / 67.4 | 1.85 / 68.6 | 2.06 / 67.1 | 2.06 / 67.3 |
| es>en | 2.07 / 60.6 | 2.06 / 61.8 | 2.09 / 60.9 | 2.00 / 61.4 |
| sw>en | 2.57 / 65.1 | 2.51 / 65.7 | 2.06 / 67.1 | 2.15 / 68.0 |

Per pair, pipeline minus baseline in MetricX (negative: the pipeline is better):

| pair | Qwen | Gemma |
|---|---|---|
| en>am | – | -0.36 |
| en>ar | -0.09 | -0.06 |
| en>de | -0.05 | +0.09 |
| en>es | +0.02 | -0.03 |
| en>fr | -0.00 | +0.02 |
| en>ha | +0.96 | -0.02 |
| en>hi | +0.12 | -0.23 |
| en>id | +0.02 | +0.02 |
| en>ja | -0.15 | +0.02 |
| en>km | -0.75 | +0.03 |
| en>ne | -0.24 | -0.17 |
| en>ru | +0.00 | +0.12 |
| en>sw | -0.29 | -0.02 |
| en>tr | +0.25 | +0.11 |
| en>uk | -0.07 | -0.07 |
| en>vi | +0.01 | +0.05 |
| en>yo | – | +0.08 |
| en>zh-Hans | +0.07 | -0.06 |
| ar>en | +0.14 | -0.00 |
| es>en | +0.01 | +0.09 |
| sw>en | +0.06 | -0.09 |
| **mean** | **+0.00** (19 pairs, better on 8) | **−0.02** (21 pairs, better on 11) |

Cost per pair, averaged over the pairs each system finished:

| system | tokens in | tokens out | seconds |
|---|---|---|---|
| qwen-pipeline | 112,548 | 55,915 | 306 |
| qwen-baseline | 6,592 | 7,896 | 30 |
| gemma-pipeline | 78,173 | 43,491 | 388 |
| gemma-baseline | 8,260 | 8,828 | 116 |

Gemma's calls were slower on OpenRouter's providers than Qwen's; the times say more about the
providers than the models.

### Failures

| system | pair | what happened |
|---|---|---|
| qwen-pipeline | en>am | the guide (full, then light) ran to the output limit, two attempts out of two |
| qwen-baseline | en>am, en>yo | a chunk ran to the output limit; 30–100 sentences untranslated, three attempts out of three |
| qwen-pipeline | en>tr, en>yo, en>km | failed the same way once, then finished |
| gemma-baseline | en>hi | a chunk ran to the output limit once, then finished |

The truncations are repetition loops, not long answers. A saved Qwen output repeats "'Bottled
water' is 'omi ti a paapaa' is not right" until the limit. The pipeline survives a loop in
translation by splitting the chunk and retrying; the baseline cannot.

### Qwen with its recommended sampling

Qwen's own guidance for non-thinking use is temperature 0.7 with `presence_penalty: 1.5`
against repetition. A side run on the six pairs where Qwen had failed (`coglossa.qwen-tuned.yaml`):

| pair | tuned pipeline | tuned baseline | default pipeline | default baseline |
|---|---|---|---|---|
| en>yo | 9.33 / 26.9 | 9.71 / 25.2 | 6.98 / 22.8 | failed |
| en>am | 5.70 / 27.4 | 14.28 / 10.4 | failed | failed |
| en>ha | 7.67 / 41.6 | 6.28 / 43.5 | 6.10 / 45.3 | 5.14 / 46.4 |
| en>sw | 4.97 / 56.2 | 5.22 / 56.9 | 3.51 / 58.7 | 3.80 / 59.0 |
| en>tr | 2.88 / 57.7 | 2.97 / 58.6 | 3.13 / 59.5 | 2.88 / 60.1 |
| ar>en | 2.07 / 66.7 | 2.10 / 66.1 | 1.99 / 67.4 | 1.85 / 68.6 |

MetricX / chrF; the default columns are the main run's. Within each run, MetricX is over the
sentences every finished system of the pair scored.

The loops stopped, and every pair finished, but quality on low-resource targets fell: with these
settings the baseline's Amharic switches into English in the middle of 63 sentences out of 100.
Qwen3.6 is not a good model for Yoruba, Amharic or Hausa at any of these settings.

## Bugs found and fixed

1. **Bare language codes sent a model into the wrong language.** The prompts said "into ha";
   Gemma's baseline wrote Yoruba for Hausa (MetricX 9.1, chrF 14). Every `*_lang` prompt
   variable now renders as a name with its code, "Hausa (ha)", in built-in and override
   templates (`coglossa/languages.py`). All results above were made after this fix.
2. **A judge that copied an excerpt loosely lost the whole chunk.** Gemma quoted with "..." for
   the translation's "…", shortened a quote with an ellipsis, left out the zero-width spaces
   between Khmer words, and composed Yoruba accents the text had decomposed; each time the
   excerpt check failed the chunk after repair. A third of en>es spans and 390 of 439 Khmer
   spans went unjudged. Excerpts now match through plain quotes, dashes and spaces, accent
   composition, doubled accents, invisible format characters and elisions (and a quoted
   ellipsis is kept as quoted), and are returned as the exact text of the translation.
3. **Judging a whole chunk at once overwhelmed a mid-size judge.** With about a hundred
   sentences in one call, Gemma merged span ranges and ran out of output. The judge now works
   in batches of 20 spans, concurrently, and a failed batch costs only its own spans.
4. **The scorer's memory grew until it was killed.** Under bench load the CPU scorer grew from
   1 GiB past the 8 GiB container limit. Each request thread had its own glibc arena;
   `MALLOC_ARENA_MAX=2` in the scorer image held it at 3–6 GiB.
5. **A guide cut off by the output limit failed the document.** A guide that outgrows the
   limit now falls back to a light guide. (A light guide can still loop; see follow-ups.)

## Flags and calibration

`coglossa bench flags` ran the real scoring stage over the FLORES reference translations, clean
and with one injected error each (an omission, a changed number, a swapped name, a reversed
negation, a register shift, or a broken locked term; negations and register shifts were written
by Qwen3.6). Gemma 4 judged and back-translated, the CPU MetricX scorer added quality estimates,
and Qwen3.6 counted as the translator, so the judge was independent of it. That is 8,738 spans
over the 21 pairs, with each pair in its shipped tier: tier A is es, zh-Hans, ru, fr, de, ja and
es>en; tier B is ar, hi, tr, vi, uk, id and ar>en; tier C is sw, yo, am, ne, km, ha and sw>en.

`coglossa bench calibrate` then set each meaning threshold to the highest that flags at most 10%
of the clean spans in its tier (with `low_meaning` or a critical error), and the style threshold
the same way:

| tier | meaning threshold | clean spans flagged | omission | number | name | negation | recall (mean) |
|---|---|---|---|---|---|---|---|
| A | 86 | 9% | 100% (685/688) | 49% (79/160) | 98% (130/133) | 96% (642/669) | 86% |
| B | 84 | 9% | 99% (647/656) | 46% (63/137) | 97% (97/100) | 96% (623/651) | 84% |
| C | 70 | 10% | 93% (610/655) | 29% (39/134) | 81% (119/147) | 68% (438/642) | 68% |

The style threshold is 100: a span is flagged for style when the judge finds any style error.
That flags 4% of clean spans and catches 39% of register shifts (457/1,182).

These are now the defaults (`scoring.thresholds`, was A 70, B 72, C 75, style 60). The numbers
differ by tier because each tier weighs the signals differently (tier C leans on the judge and
back-translation, not MetricX), so they are not comparable across tiers; what is the same is the
rate at which correct passages are flagged. At the old defaults, tier A flagged almost no clean
spans but caught 59% of negations and 8% of changed numbers, and tier C flagged 17% of clean
spans.

Read these numbers with their limits:

- **419 spans (5%) were left out** because a judge batch failed for them: Gemma quoted text
  that is not in the span, merged two spans, or ran to the output limit. Such failures are more
  likely where the judge found something, so recall is, if anything, understated. (The flag
  test first marked every span of a round when one batch of it failed; the records were
  corrected to mark only the failed batch's spans, which the saved judge signal shows, before
  this calibration, and `bench flags` now does so itself.)
- **The references are human translations**, not model output, and carry no agreement signal
  (there are no translators to agree), so real runs have one signal more.
- **The judge model matters.** These thresholds hold for a judge and back-translator like Gemma
  4. Another judge needs its own calibration; `coglossa.gemma-scoring.yaml` (Qwen3.6 judging) is
  ready for that run.
- A run whose judge or back-translator is also a translator is not independent (spec §6.4), and
  its spans drop one tier, so it is held to the next tier's threshold.

`bench calibrate` also suggested new tiers from the Gemma pipeline's MetricX: every pair below 5
(all but en>yo) in tier A, Hausa, Amharic and Khmer included. The shipped tiers are kept. A
tier says how far the scores can be trusted for a language, which one model's good translations
do not show, and the thresholds above were fitted with the shipped tiers; moving a pair would
need another `bench flags` and `bench calibrate` round.

### The numbers check (CGL-86, November 2026)

Changed numbers were the weak spot, so a rule now compares the numbers written in digits in the
source and the translation, in any digit script and separator style, and raises
`number_mismatch` when they differ (README, "Flags"; `coglossa/scoring/numbers.py`). It is a
hard trigger and leaves the scores, and so the thresholds above, unchanged. `bench flags` counts
it as a meaning flag from records version 2 on, so saved pairs are scored again.

`bench flags` was not run again with the models (that needs the OpenRouter models and the
scorer). Instead the run's clean and number-injected spans were rebuilt from FLORES-200 with the
same sample, seed and injection code, and the check was run on them alone:

| tier | changed numbers it catches | clean spans it flags |
|---|---|---|
| A | 91% (147/162) | 0.3% (2/700) |
| B | 97% (145/149) | 0.1% (1/700) |
| C | 99% (142/144) | 1.0% (7/700) |

(The table above counts 160, 137 and 134 changed numbers because it leaves out the spans whose
judge batch failed; the check needs no model, so here every span counts.) A span is caught when
any flag fires, so number recall with the check on is at least these figures, and the clean
false-positive rate rises by at most these. Every number the check misses is one the source
has only in words ("three people", "in August", "a year") or not at all, while the reference
writes it in digits; that is the documented limit. Most clean spans it flags are real
differences in the human references (a year written 1089 for 1989, a dropped year, a reference
for another sentence), a unit conversion (pounds to kilograms), or Yoruba references that spell
large numbers out.

Over all 1,012 devtest sentences of the same pairs (the same offline, rule-only rerun), the check
catches 88%, 97% and 99% of changed numbers (1,295/1,477, 1,326/1,370, 1,290/1,301) and flags
1.0%, 0.6% and 0.8% of clean spans (70, 45 and 59 of 7,084). There the clean flags are mostly
12-hour times written as 24-hour ones (11 pm, 23 h; 1200 GMT against 12:00), decades written
with their century (the 1960s, 20世纪60年代), and again spelled-out Yoruba numbers.

From records version 3 (CGL-114) the check reads a lone comma or point before three digits the
way each side's language writes it, so English 1,500 against French 1.5 is flagged. Run on the
human references of all 21 pairs (21,252 devtest sentence pairs, rule only), it flags 174
before and 174 after: the clean false-positive rate does not move.

A scale word changes a number's size only when it stands next to the number (1.5 million,
150万, 10 آلاف), so the same digits at another size (5 days against 50) are flagged; on the full
devtest this also catches two real errors in the references (AD 1000–13000 for 1000–1300, 30,00
for 30,000).

## Recommendation for bundled models (CGL-12)

- **Bundle Gemma 4 26B-A4B as the translator and reviewer.** It was the stronger model in every
  group of languages here, by a wide margin on low-resource ones, it finished every pair, and it
  is Apache-2.0.
- **Do not bundle Qwen3.6-35B-A3B as a translator for low-resource languages.** At the default
  settings it loops; at its recommended settings it drifts into English.
- **The judge and back-translator must be a second model** (spec §6.4), or every span drops a
  tier. This run measured Gemma 4 as the judge of Qwen's translations. For a Gemma bundle, the
  second model still has to be chosen and its flags calibrated: Qwen3.6 is a candidate for
  high-resource pairs only.
- **Measure the pipeline on documents before deciding what to bundle for cost.** On single
  sentences it costs 7–12 times the tokens of one pass for the same quality. Its guide and
  consistency checks are for long texts, which FLORES does not test (the long-form dataset was
  deferred in M6).
- **Customers with their own models** (the air-gapped route) should run `coglossa bench` on their
  languages first. This run shows what to look for: repetition loops on low-resource languages
  (set `max_output_tokens`), and judge failures that leave spans unchecked.

