# M2 follow-ups

These came out of the M2 per-task reviews and the final review. They are open, not fixed.

## Resolved
- `term_in` word boundaries next to CJK, kana, Thai and Hangul text, plugin scorers without the `supports_*` attributes, and the U+F900 range: fixed before the M2 merge.
- **Real-weights check (2026-10-10):** `scorer/tests/test_real_models.py` passed against `google/metricx-24-hybrid-xl-v2p6` (converted to safetensors on the Intel Mac, see README), the `google/mt5-xl` tokenizer and LaBSE. That includes a reference-mode test and a batch-invariance test.
  - **MetricX ranking** (0–25, lower is better):
    - "Il pleut à Paris." → correct 2.9, negated 11.2, unrelated 13.7.
    - Swahili: correct 1.1, wrong 9.4.
    - Chinese: correct 0.0, wrong 1.4.
    - Reference mode: faithful back-translation 1.8, unrelated 9.1.
  - **Weak spot:** MetricX barely separates entity or number substitutions (Jan → March: 1.3 → 2.1). The judge and roundtrip signals must catch those. M6 calibration should check flag recall on them.
  - **LaBSE cosine:** fr/en 0.78, zh/en 0.96, sw/en 0.96, unrelated 0.08.
  - **CPU speed:** about 0.8 s per QE item on the Intel Mac, so recommend a GPU for long documents.
- **Ship MetricX as safetensors in the bundle (resolved in M5):** `coglossa-scorer-weights` (`make weights`, M5 Task 6) converts MetricX's `.bin` shards to safetensors and deletes the pickles, so the bundle carries no pickled weights.
- **Stale wording (resolved in M3):** the README and the CLI checkpoint message said locked terms are "not yet checked automatically"; both now describe the sweep.

## Should fix soon
- **Similarity budget too large for CJK:** the 800-character `sim_max_chars` limit is too high for CJK text, where LaBSE uses about one token per character. Use a budget that depends on the script.

## Deferred minor findings (verbatim from the review ledger)
- Task 1: minor (deferred): notices comment placement after ruff wrap; no 256-accept / 257-pair reject tests; response models unbounded (client checks counts in T3)
- Task 2: minor (deferred): unquoted `no` in custom tiers silently becomes "false"; inf weights pass ge=0; scoring: null no longer accepted; nn/iw/fil absent from tier B; top-level falsy non-mapping YAML treated as empty
- Task 3: minor (deferred): HttpScorer(max_batch<=0) direct construction raises raw ValueError; plugin load/factory errors unwrapped; no timeout test; some tests don't aclose clients; fake server bad Content-Length; err['msg'] not _one_line'd
- Task 4: minor (deferred): guide_check substring match ("Ana" satisfied by "banana"); tests lack blank final / empty final ratio cases; judge_penalties KeyError if severity missing (ScoringConfig merges defaults so all present)
- Task 5: minor (deferred): misattribution test doesn't assert 2 requests (repair); no judge_chunk-level mismatched-range test; whitespace-only omission excerpt fails validation; exact (non-NFC) excerpt match
- Task 6: minor (deferred): failure test doesn't assert chunk_id/message; no repair-path test at stage level; model returning "" back-translation → "" not None (T8 bt_live filters falsy texts, so no signal)
- Task 7: minor (deferred): flag text uses :.0f so 69.6 reads "70 is below the 70 threshold" (use .1f); style fallback wording mentions source comparison; score_span no tier validation (UNSCORED → KeyError); no A/B threshold tests
- Task 8: minor (deferred, IMPORTANT-ish for final triage): constant FakeScorer can't catch index misalignment — add blank-first + distinct qe_overrides alignment test; partial scorer failure (qe fails, sim ok) untested; scorer-ok/roundtrip-fail path untested; blank span absence only checked in qe call
- Task 8: minor (deferred): scorer down costs two timeout rounds per chunk (skip 2nd round if calls.error); tier-C notice shown even when nothing scored (plan-mandated); "" back-translation reported as "" not None; `i in live` list lookup; no RED evidence
- Task 9: minor (deferred): `where` fallback "unknown" unreachable; fake-scorer CLI test checks banner only; notice ordering vs progress not asserted
- Task 10: minor (deferred): LabseEmbedder.from_pretrained lacks local_files_only (relies on CLI HF_HUB_OFFLINE); torch_dtype="auto" deprecated in transformers 5 (Linux lock); [tool.uv] block ordering; no LaBSE clip test
- Task 11: minor (deferred): app doesn't check model returns one score per item (engine client checks); setdefault lets HF_HUB_OFFLINE=0 override (plan-mandated); no concurrency/lock test
- Task 12: minor (deferred): no paths filter/concurrency in CI (scorer job installs torch every push)
- Final: minor (deferred): stale README/CLI "locked terms not checked" wording; text_metrics.py spaceless range starts at U+8C48 not U+F900 (pre-existing, also covers Hangul/PUA); LaBSE 800-char budget too large for CJK (≈1 token/char)
