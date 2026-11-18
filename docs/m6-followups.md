# M6 follow-ups

## Spec deviations in M6
- **The literary long-form dataset is deferred.** Spec §9.2 names "public-domain literary works
  with published translations" for long-form text. Aligning such works by paragraph is a project
  of its own. M6 ships FLORES-200 and a generic `parallel` dataset kind (a source file and a
  reference file, aligned line by line), so a literary pair can be added later as data, without
  code.

## Calibration run (Task 12)
Run on 2026-10-13 on OpenRouter with Qwen3.6-35B-A3B and Gemma 4 26B-A4B; results, the
calibrated thresholds, and the bundled-model recommendation are in
`docs/bench-results-2026-10.md`, and the configs in `bench/m6-calibration/`.

## Fixed in the final-review fix wave
The final review's Critical and Important findings and its chosen minors are fixed on
`m6-bench` (`.superpowers/sdd/2026-10-12-m6-bench/final-fix-report.md` maps each to its commit
and test): thresholds by clean false-positive rate with recall per kind (C1, I2), scoring
errors per span (I3), precision explained (I4), resume by fingerprint (I5), a concurrent
baseline (I6), MetricX over shared units with coverage columns (I7), mixed judging rounds
(I8), the default tier system from the bench config's order (I9), pending MetricX re-scored on
resume (I10), and the minors (spaceless joins, systems checked up front, blank lines refused,
injection samples and the lower-bound note, the tier/threshold order in the guide, the
baseline-first fixture, ordered tier cuts).

## Fixed before the calibration run
Fixed on `m6-calibration-prep` (plan `docs/superpowers/plans/2026-10-13-m6-calibration-prep.md`):
- **The resume fingerprint covers only what shapes the text** (CGL-8). For a pipeline it hashes
  the backends' kind and URL, the models, roles, chunking, profiles and the text of every prompt
  template; for a baseline only its own model, that model's backend, chunking, the chunk size
  the roster of models allows, and the `contextual` prompt. Thresholds, tiers, timeouts, concurrency, retry, audit and the scorer no
  longer re-translate anything.
- **MetricX is re-scored by its own identity** (CGL-10): each result records the QE model and
  `qe_max_chars` that scored it (`metricx_by`). A scorer that is down or has no QE model leaves
  results pending, and a run with another MetricX model re-scores them, without translating.
- **`bench ab` reads only what the last summary read** (CGL-10), from `summary.json`.
- **The two clean false-positive rates are named** (CGL-9): `calibration.yaml`, the README and
  the operator guide say a threshold's rate counts its own flag plus critical errors, and
  `flag-summary.md` says its rate counts any flag.
- **Per-kind recall shows its counts** (CGL-9), and the average leaves out kinds with fewer than
  10 injected spans.
- **Models take server options** (`extra_body`, added for the run): Qwen3.6 reasons by default,
  which multiplies tokens; `reasoning: {enabled: false}` (OpenRouter) or
  `chat_template_kwargs: {enable_thinking: false}` (vLLM) turns it off.

## Fixed during the calibration run
On `m6-calibration`:
- **Prompts name languages** ("Hausa (ha)", not "ha"): a model asked for "ha" wrote Yoruba.
  Every `*_lang` prompt variable, in built-in and override templates, goes through
  `coglossa/languages.py`; bench fingerprints carry `RENDER_VERSION`, so older results are redone.
- **Judge excerpts are located, not matched exactly**: plain quotes, dashes, spaces and "..."
  elisions match the translation's own text, and the excerpt returned is that text.
- **The judge works in batches of 20 spans**, concurrently; a failed batch costs its own spans,
  not the chunk.
- **A cut-off full guide falls back to a light guide.**
- **The scorer image sets `MALLOC_ARENA_MAX=2`**: its memory grew past 8 GiB under bench load.
- **`bench flags` scores a pair's rounds concurrently.**

## Fixed after the calibration run
On `engine-model-robustness` (CGL-14, CGL-15, CGL-21):
- **A guide that cannot be built no longer fails the document.** When the guide model's answer
  is unusable (invalid, cut off by a repetition loop, or too large), the run translates with the
  earlier guide, or without one, records why, and does not stop for a review of a guide it does
  not have. A full guide falls back to a light one first; partial guides that cannot be merged
  are combined as they are. A model that cannot be reached still fails it. Capping the guide's
  lists (`maxItems`) would not have helped: the saved loops repeat a phrase inside one string.
- **Translations are no longer cut off at quotation marks.** Under constrained decoding, a plain
  `"` a model types inside a JSON string ends the string: every contextual failure in the bench
  was this (s92, the word "Gypsy" in quotes), and one passed as a one-word translation. Prompts
  that write text ask for typographic quotation marks, and a text that ends with a space is
  refused and repaired.
- **A span list written twice, or a blank span past the last segment, is accepted** (the extra
  spans are dropped); a span past the end that says anything new is still refused.
- **One bad judge answer no longer costs a batch of 20.** If excerpts are still wrong after the
  repair turn, an error moves to the one other span its excerpt (four characters or more)
  quotes, or else stays without a highlight; merged ranges or a cut-off answer halve the batch
  and judge again, twice at most.

## Open after the calibration run
Triaged on 2026-10-30 (CGL-13): each item names its ticket, or why it has none.
- **Repetition loops** still cost time (CGL-57, a decision for the owner): a loop runs to the
  output limit (16,384 tokens) before the pipeline can recover. Detecting a loop early needs streaming. Qwen's recommended sampling
  (0.7, `presence_penalty: 1.5`) stopped the loops but made its low-resource output switch into
  English.
- ~~**The chunk budget ignores the output limit** (CGL-56). `compute_budget` sizes chunks by
  context window and words; a chunk whose translation needs more than `max_output_tokens` (likely for scripts
  that tokenize poorly, such as Ethiopic) can only be cut off and split.~~
  Done in CGL-56: the chunk's token budget is also capped at the smallest `max_output_tokens` of
  the roles whose answer grows with the chunk (the translators, the reviewer and the roundtrip),
  divided by the 1.5x margin reconcile already assumes; the judge, sweep, profile and guide are
  left out. With the 16,384 of the calibration configs and the hosted preview it falls from
  13,896 to 10,922 tokens, which moves the calibration baselines' fingerprints (they hash the
  budget, not the chunks), though 100 FLORES sentences close on words long before either: the
  next `bench run` on an old `out/` translates the baselines again. A limit too small for one
  segment is a config error naming the role and the limit (the tightest limit first). The
  margin is on the source's token estimate, so a target script that tokenizes far worse than
  its source (English into Amharic) can still exceed the limit, and the pipeline still splits it.
  With CGL-57, a role's own `max_output_tokens` counts where it is lower than its model's, and
  the error then names the role's limit; the judge's 8,192 default does not touch the budget.
- **The bench baseline has no split-and-retry**, so one runaway chunk fails its whole pair. That
  is fair to a one-call baseline, but it makes the baseline's failures partly a property of the
  harness. No ticket: kept as is, since a baseline with retries is no longer one call.
- **The flags were calibrated with Gemma 4 judging.** A bundle that judges with another model
  (Qwen3.6 with Gemma translating, say) needs its own `bench flags` run;
  `bench/m6-calibration/coglossa.gemma-scoring.yaml` is ready for it. No ticket: it waits on the
  bundled-model decision (CGL-12).
- **Tiers were not recalibrated.** `bench calibrate` suggested tier A for every pair under MetricX
  5 with Gemma 4 (Hausa, Amharic and Khmer included); the shipped tiers are kept, since a tier is
  about how far the scores can be trusted, and moving pairs means another flags-and-calibrate
  round. No ticket: kept on purpose; a recalibration follows the next `bench flags` run.
- **`bench flags` runs pairs one after another** (CGL-16), about 40–60 minutes a pair on
  OpenRouter with a CPU scorer. Running pairs concurrently, and resuming finished pairs, would make on-site
  calibration practical. Several bench processes sharing one CPU scorer also need a longer
  `scorer.timeout_s`: requests queue behind each other past the default 300 s.
- ~~**The OpenAI-compatible client's timeout is per read** (CGL-55), so a slow stream is not
  bounded in total; one OpenRouter call took 1,362 s with `timeout_s: 300`.~~ Done in CGL-55: a
  backend's `timeout_s` bounds each request as a whole, the answer's body included; a request
  that runs out of time fails as a timeout and is retried like one.

## Left out on purpose, or open
Triaged on 2026-10-30 (CGL-13): every item here stays as it is, with no ticket. The joint
meaning-and-style target waits for live false-positive numbers from a customer install, and the
spaceless-script list grows when someone checks a language's references.
- **Calibrate models meaning flags only.** Its clean false-positive rate counts the threshold
  flag and critical accuracy errors, per tier, as the ruling asks. A clean sentence can also
  be flagged by `terminology_violation`, `possible_omission` or `style_drift`, which
  `flag-summary.md` counts, so the live false-positive rate can sit above the calibrated
  target. Worth a joint target across meaning and style once Task 12's numbers show how much
  the other flags add.
- **Locked-term tests that share their term are left out.** In the mixed rounds, a locked
  entry would also judge any other sentence of its chunk whose source has the term; such a
  locked-term test is left out (and logged) rather than let it bias the other kinds' recall
  and the clean false-positive rate. On FLORES this drops few tests; the log counts them.
- **The own MetricX mean is unchanged.** `metricx_mean` in `summary.json` is still the plain
  mean over the units the system scored; only the comparable, shared mean in `summary.md` is
  weighted by sentence count.
- **"Too long by its output"** means the unit would have fit with an output as long as its
  reference; anything else counts as too long by its source.
- **A result stays pending until a QE-capable scorer scores it** (`metricx_pending: true` in
  `summary.json`). A change of MetricX model or `qe_max_chars` re-scores it, without translating.
  Results written by an earlier version have an older fingerprint, so the next run translates
  them again ("written by an older version" in the log); they are not just re-scored.
- **The summary leaves out removed pairs as well as removed systems**, so stale results of
  either never reach `summary.md` or `--tier-system`.
- **Cantonese (`yue`) is joined without spaces** with the six listed languages; other
  spaceless scripts (Tibetan, for one) are not, until someone checks their references.
