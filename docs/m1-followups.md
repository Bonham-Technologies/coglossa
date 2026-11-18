# M1 follow-ups

Minor findings from the M1 per-task and final reviews that were deferred rather than fixed. The final reviewer triaged them as non-blocking. Items marked "should fix soon" are worth doing before or during M2.

## Should fix soon
- ~~DOCX: nested tables, footnotes, headers and text boxes are silently dropped; warn or record a RunError when content is skipped.~~
  Done in CGL-87: footnotes, endnotes, headers, footers, text boxes, nested tables, content
  controls and tracked insertions are read; charts, diagrams, embedded objects and documents,
  equations and comments are named in a document notice (`unread_content`) on the run and in its
  report; it is not a run error, so a file with a comment or a chart still finishes complete.
- Guide stage: the prior guide size is not counted in the single-pass vs map-reduce decision or the merge size estimate.
- config_hash includes an absolute prompts_dir path and no template content hash; there is no short_max_words <= standard_max_words check.
- coglossa.example.yaml timeout_s=120 is a read timeout on a non-streamed reply; document that it must cover a whole chunk, or raise it.
- The default output directory coglossa-out/<stem> overwrites earlier runs and collides for same-named files.
- The reviewer output schema allows agreement "unknown", which is meant to be engine-only.

## Per-task deferred minors (verbatim from the review ledger)
- Task 1: minor (deferred): ci.yml push trigger only on main (plan-mandated; branch CI only via PR)
- Task 2: minor (deferred): test_export read_text() without encoding (plan-mandated); export default ../schema cwd-relative; no Segment start<=end validation
- Task 3: minor (deferred): config_hash includes absolutized prompts_dir (machine-dependent hash); no max_delay>=base / short<=standard checks; read_text UnicodeDecodeError/OSError not wrapped; tests lack unknown-model-in-extra_translators/judge, empty YAML; _write annotation
- Task 4: minor (deferred): malformed `usage` (non-dict/non-int) escapes BackendError contract (openai_compat _parse_chat); eager plugin ep.load() can raise raw ImportError; clients not closed if later backend build fails; overflow markers checked on any 4xx; test gaps (no-auth header, trailing slash, 401 permanent, ConnectTimeout)
- Task 5: minor (deferred): _ID_RE.match accepts trailing newline (use fullmatch); guide depth typo → "full" branch; retry tests loose (jitter floor at attempt 4, sleeps not compared to delay()); override dir with no v*.j2 branch untested; templates recompiled per render
- Task 6: minor (deferred): fake_openai json.loads outside try (malformed body → dropped conn not 400; TypeError not caught); stop() leaks socket if never started / start() twice; FakeBackend payload["task"] KeyError vs ValueError; HTTP tests lack guide_merge/reconcile
- Task 7: minor (deferred): repair turn record shows attempt=1 like first call (retry vs repair indistinguishable in audit log); failed attempts logged latency_ms=0; schema-invalid replies logged ok=True with no validation error recorded; extract_json takes first fence even if non-JSON; tests lack exhausted-transient, sleep-called, store_io=False on failures; unknown model_key → bare KeyError
- Task 8: minor (deferred): plugin ep.load()/ingest exceptions bypass IngestError; NUL-byte binary passes read_utf8; scene-break regex \s spans newlines; nested DOCX tables dropped, iteration outside try; file read twice for sha; test gaps (non-UTF-8 md via registry, successful plugin ingest)
- Task 9: minor (deferred): split_sentences max_chars<=0 loops forever (validate ≥1); hard cut can split grapheme clusters; count_words counts CJK punctuation (。) as words (plan-mandated); misleading "skip opening quotes" comment over _CLOSERS; run-level decision may change splits like "Dr.. Smith" (per R6)
- Task 10: minor (deferred): empty-block skip (chunking.py ~L88) untested; chunker silently ignores segments not tied to non-break doc blocks (precondition undocumented); error test doesn't assert remediation text; smallest-model tie picks first
- Task 11: minor (deferred): build_sample head/tail can overlap (duplicate segment) near cutoff; single huge segment unbounded in sample; tests lack payload assertions / empty input / __cause__ check
- Task 12: minor (deferred): recursive merge gather orphans sibling on failure (TaskGroup); prior_guide repeated in every partial/merge call not counted in size estimate; apply_locked keeps duplicate locked prior entries (test name says "never duplicates"); guide_slice substring w/o word boundary; raise-result + single-partial-merge branches uncovered; unused fake_backend param in new test
- Task 13: minor (deferred): no test for repair still failing coverage (assert no literal candidates leak + RunError); scripting tests rely on gather start order (deterministic today: runner has no await before backend.chat, FakeBackend.chat never suspends); build_chunk_inputs bare KeyError on unknown ids
- Task 14: minor (deferred): fallback test for non-contextual path asserts only chosen_from; MAJOR+cultural flags share rationale text (plan-mandated); competing_candidates KeyError on unknown ids undocumented
- Task 15: minor (deferred): no pipeline test for all-translators-fail path; guide chunk errors not asserted in GuideCheckpoint/Report.errors; guide_approved=True with guide=None silently regenerates; gather without return_exceptions leaves siblings running on bug-class error; _assemble mutates passed errors list
- Task 16: minor (deferred): checkpoint "next step" command omits --out/--config/--source-lang (plan-mandated); out-dir mkdir/writes OSError → traceback; README fake-openai & not killed; port-9 dead-backend test machine-dependent
- Final: minor (deferred): stale guide_errors.json not removed on later clean checkpoint; out dir created before ingest leaves empty dir on bad input; guide-stage errors not carried into resumed report (accepted in R10)
