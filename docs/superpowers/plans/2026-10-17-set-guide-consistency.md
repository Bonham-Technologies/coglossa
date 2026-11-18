# Names agree across a set's files (CGL-23) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Files of one set (an engine *project*) render a shared name or term the same way on the first pass, with no added waiting. A rule a person adds while a file translates also reaches that file.

**Architecture:** The set guide becomes the newest guide version that is approved or is the
draft of a run still in use. Each run's draft is merged onto that set guide when it is saved,
under the project row lock that saves already take. Set entries win; the draft adds new keys
and keeps its own summary and style guide. The final sweep uses the set guide with the same
merge.

**Tech Stack:** Python 3.12, SQLAlchemy async (Postgres), pytest + hypothesis, ruff. Engine only; a one-line comment in the web app.

**Spec:** `docs/superpowers/specs/2026-10-17-set-guide-consistency-design.md`

## Global Constraints

- Earlier renderings win: once a term or name is in the set guide, later drafts keep its entry exactly (rendering, notes, gender, `locked`).
- People's entries win over the engine's; only people lock entries.
- No stage waits for another; translation stays parallel.
- No new API endpoints, screens or migrations.
- Merge keys: glossary `term` and entity `name`, casefolded.
- The draft keeps its own `summary` and `style_guide`.
- Drafts of runs that are `failed`, `cancelled` or `awaiting_guide_approval` are never the set guide.
- Lock order in any transaction that takes both: the run row first, then the project row (as `service.approve_guide` and `review.add_guide_entry` already do).
- Engine tests need Postgres: `make db-up` once; single files run with
  `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest <path> -q --no-cov`
  (`--no-cov` because the package's coverage gate applies only to the full suite).
- Lint before each commit: `cd engine && uv run ruff check . && uv run ruff format --check .`
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012TBMbjzsQyykrdRBR7Vuv5
  ```

## Review Focus

1. Two or more files of a new set build their guides at the same moment with different renderings of one name. Every file should translate with one rendering, the first one saved. *(Task 3, `test_files_translated_together_agree_on_names`)*
2. A person adds or unlocks a rule while a file translates. That file's final sweep should follow the set guide as it is then. *(Task 4, two tests)*
3. A failed, cancelled or awaiting-approval run's draft must never become the set guide, neither as a new run's prior nor as a merge base. *(Task 2, `test_prior_guide_is_the_newest_approved_or_usable_draft`)*
4. A guide-panel save opened before a file's draft landed should be refused with the existing "guide changed" error, not overwrite the draft's entries. *(Task 2, `test_the_project_guide_is_the_newest_usable_version`)*
5. A person saves the set guide while a checkpointed file's guide is being built. The draft should keep that person's locked entry, so approving the draft as-is drops nothing. *(Task 3, `test_a_draft_keeps_what_a_person_saved_while_it_was_built`)*

---

### Task 1: `carry_over`, the merge rule

**Files:**
- Modify: `engine/coglossa/stages/guide.py` (add `carry_over` after `apply_locked`, around line 235)
- Test: `engine/tests/stages/test_guide.py`

**Interfaces:**
- Produces: `carry_over(base: Guide | None, guide: Guide) -> Guide` in `coglossa.stages.guide`. Tasks 3 and 4 call it.

- [ ] **Step 1: Write the failing tests** (append to `engine/tests/stages/test_guide.py`; add `carry_over` to the existing `from coglossa.stages.guide import ...` line)

```python
def test_carry_over_keeps_the_set_guides_entries_and_adds_new_ones():
    base = Guide(
        summary="first file",
        style_guide="first style",
        glossary=[GlossaryEntry(term="Chat", rendering="cat", notes="set", locked=True)],
        entities=[Entity(name="Anna", rendering="Anna", kind="person", gender="f")],
        cultural_references=["the cafe"],
    )
    draft = Guide(
        summary="second file",
        style_guide="second style",
        glossary=[
            GlossaryEntry(term="chat", rendering="kitty"),
            GlossaryEntry(term="chien", rendering="dog"),
        ],
        entities=[
            Entity(name="ANNA", rendering="Anne", kind="person"),
            Entity(name="Paris", rendering="Paris", kind="place"),
        ],
        cultural_references=["the cafe", "the metro"],
    )
    merged = carry_over(base, draft)
    assert merged.glossary == [base.glossary[0], draft.glossary[1]]
    assert merged.entities == [base.entities[0], draft.entities[1]]
    assert merged.cultural_references == ["the cafe", "the metro"]
    assert (merged.summary, merged.style_guide) == ("second file", "second style")


def test_carry_over_without_a_set_guide_is_the_draft():
    draft = Guide(summary="s", style_guide="g", glossary=[GlossaryEntry(term="a", rendering="b")])
    assert carry_over(None, draft) == draft


@given(
    st.lists(st.tuples(terms, st.booleans()), max_size=8),
    st.lists(terms, max_size=8),
)
def test_carry_over_never_changes_or_drops_a_set_entry(base_terms, draft_terms):
    base = Guide(
        summary="",
        style_guide="",
        glossary=[GlossaryEntry(term=t, rendering="p", locked=locked) for t, locked in base_terms],
    )
    draft = Guide(
        summary="",
        style_guide="",
        glossary=[GlossaryEntry(term=t, rendering="e") for t in draft_terms],
    )
    merged = carry_over(base, draft)
    assert merged.glossary[: len(base.glossary)] == base.glossary
    known = {g.term.casefold() for g in base.glossary}
    added = merged.glossary[len(base.glossary) :]
    assert added == [g for g in draft.glossary if g.term.casefold() not in known]
```

(`terms` is the hypothesis strategy already defined in this file, above `test_apply_locked_never_drops_or_duplicates_locked_terms`. Place these tests after it.)

- [ ] **Step 2: Run them to verify they fail**

Run: `cd engine && uv run pytest tests/stages/test_guide.py -q --no-cov -k carry_over`
Expected: collection error, `ImportError: cannot import name 'carry_over'`.

- [ ] **Step 3: Implement** (in `engine/coglossa/stages/guide.py`, directly after `apply_locked`)

```python
def carry_over(base: Guide | None, guide: Guide) -> Guide:
    """A run's draft merged onto the set guide (CGL-23). Every term and name the set guide has
    stays exactly as it is, so the files of one set agree on them; the draft adds only the terms
    and names the set guide lacks. The summary and style guide stay the draft's: they describe
    its own document."""
    if base is None:
        return guide
    terms = {g.term.casefold() for g in base.glossary}
    names = {e.name.casefold() for e in base.entities}
    return guide.model_copy(
        update={
            "glossary": [
                *base.glossary,
                *(g for g in guide.glossary if g.term.casefold() not in terms),
            ],
            "entities": [
                *base.entities,
                *(e for e in guide.entities if e.name.casefold() not in names),
            ],
            "cultural_references": [
                *base.cultural_references,
                *(r for r in guide.cultural_references if r not in base.cultural_references),
            ],
        }
    )
```

- [ ] **Step 4: Run them to verify they pass**

Run: `cd engine && uv run pytest tests/stages/test_guide.py -q --no-cov`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
cd engine && uv run ruff check . && uv run ruff format --check . && cd ..
git add engine/coglossa/stages/guide.py engine/tests/stages/test_guide.py
git commit -m "feat(engine): carry_over merges a draft onto the set guide (CGL-23)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012TBMbjzsQyykrdRBR7Vuv5"
```

---

### Task 2: the set guide is the newest usable version

**Files:**
- Modify: `engine/coglossa/store/runs.py:193-212` (`FINISHED_STATES` stays; `prior_guide` rewritten; new `UNUSABLE_DRAFT_STATES`, `usable_guide_version`)
- Modify: `engine/coglossa/catalog.py:50-62` (`_projects_query`'s guide subquery)
- Modify: `web/src/api/guide.ts:21` (comment only)
- Test: `engine/tests/store/test_runs.py` (replace `test_prior_guide_is_the_latest_approved_else_the_latest_finished_runs`)
- Test: `engine/tests/test_guides.py` (replace `test_a_finished_runs_guide_is_the_project_guide_until_one_is_approved`)
- Test: `engine/tests/worker/test_execute.py` (replace `test_an_approved_guide_wins_over_a_newer_draft`)

**Interfaces:**
- Produces: `runs.UNUSABLE_DRAFT_STATES: tuple[str, ...]`, `runs.usable_guide_version() -> ColumnElement[bool]` (needs the query to outer-join `Run` on `GuideVersion.source_run_id`), and `runs.prior_guide(s, project_id) -> GuideVersion | None` with the new rule. The signature is unchanged; Tasks 3 and 4 call it.

- [ ] **Step 1: Write the failing store test** (in `engine/tests/store/test_runs.py`, replace the whole `test_prior_guide_is_the_latest_approved_else_the_latest_finished_runs` function; keep `_set_state` and `_draft`; add `from coglossa import catalog` to the imports)

```python
async def _project_guide(db, project_id):
    """prior_guide's version and the catalog's (its SQL mirror); they must always agree."""
    async with db.session() as s:
        row = await runs.prior_guide(s, project_id)
    listed = (await catalog.get_project(db, project_id)).guide_version
    assert listed == (row.version if row else None)
    return row


async def test_prior_guide_is_the_newest_approved_or_usable_draft(db):
    project, _, first = await _run(db)
    _, _, second = await _run(db)
    _, _, third = await _run(db)
    assert await _project_guide(db, project.id) is None
    finished = await _draft(db, project.id, first.id, "finished")
    await _set_state(db, first.id, "complete_with_errors")
    for state in ("failed", "cancelled", "awaiting_guide_approval"):  # never, however recent
        await _set_state(db, second.id, state)
        await _draft(db, project.id, second.id, state)
        assert (await _project_guide(db, project.id)).id == finished.id, state
    await _set_state(db, third.id, "translating")
    running = await _draft(db, project.id, third.id, "still translating")
    assert (await _project_guide(db, project.id)).id == running.id
    approved = await _draft(db, project.id, second.id, "approved", status=runs.APPROVED)
    assert (await _project_guide(db, project.id)).id == approved.id  # approved, whatever its run
    newer = await _draft(db, project.id, third.id, "built on the approved one")
    assert (await _project_guide(db, project.id)).id == newer.id  # the newest usable wins
```

- [ ] **Step 2: Write the failing catalog test** (in `engine/tests/test_guides.py`, replace `test_a_finished_runs_guide_is_the_project_guide_until_one_is_approved` entirely)

```python
async def test_the_project_guide_is_the_newest_usable_version(db):
    seeded = await seed_run(db)  # complete; its guide v1 is an engine draft
    assert (await catalog.project_guide(db, seeded.project_id)).info.version == 1
    for state in (RunState.FAILED, RunState.CANCELLED):
        await seed_run(db, project="demo", state=state)  # seed_run's default project
    assert (await catalog.project_guide(db, seeded.project_id)).info.version == 1
    assert (await catalog.get_project(db, seeded.project_id)).guide_version == 1
    await seed_run(db, project="demo", state=RunState.TRANSLATING)  # its draft is v4
    versions = await catalog.guide_versions(db, seeded.project_id)
    assert [v.version for v in versions] == [4, 3, 2, 1]
    assert (await catalog.project_guide(db, seeded.project_id)).info.version == 4
    assert (await catalog.get_project(db, seeded.project_id)).guide_version == 4
    # a panel opened before that draft landed is refused rather than dropping its entries
    with pytest.raises(service.ConflictError, match="now version 4, you edited version 1"):
        await catalog.save_project_guide(db, seeded.project_id, LOCKED, base_version=1)
```

- [ ] **Step 3: Write the failing worker test** (in `engine/tests/worker/test_execute.py`, replace `test_an_approved_guide_wins_over_a_newer_draft` entirely)

```python
async def test_a_newer_draft_is_the_project_guide_and_keeps_the_approved_entries(
    services, tmp_path, monkeypatch
):
    approved = Guide(
        summary="approved",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    first = await submit(services, tmp_path, name="one.txt", checkpoint=True)
    await drain(services)
    await service.approve_guide(services.db, services.queue, first, approved)
    await drain(services)
    second = await submit(services, tmp_path, name="two.txt")  # its draft is newer
    await drain(services)
    priors = _capture_priors(monkeypatch)
    await submit(services, tmp_path, name="three.txt")
    await drain(services)
    assert priors == [await service.run_guide(services.db, second)]
    assert priors[0].glossary == approved.glossary
```

- [ ] **Step 4: Run them to verify they fail**

Run (after `make db-up` from the repo root):
`cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/store/test_runs.py tests/test_guides.py tests/worker/test_execute.py -q --no-cov -k "prior_guide or newest_usable or newer_draft"`
Expected: 3 failures. The store test fails at the `running` assertion (it gets `finished`). The catalog test fails at `info.version == 4` (it gets 1). The worker test fails because the prior is the approved guide, not two.txt's draft.

- [ ] **Step 5: Implement in `engine/coglossa/store/runs.py`**

Add `or_` to the `from sqlalchemy import ...` line and `from sqlalchemy.sql.elements import ColumnElement`. Replace `prior_guide` (keep `FINISHED_STATES` above it; other modules use it):

```python
# A draft is not the project guide while its run waits for a person's approval, nor once the
# run failed or was cancelled.
UNUSABLE_DRAFT_STATES = (
    RunState.FAILED.value,
    RunState.CANCELLED.value,
    RunState.AWAITING_GUIDE_APPROVAL.value,
)


def usable_guide_version() -> ColumnElement[bool]:
    """Which versions can be the project guide: approved ones, and engine drafts of runs that
    use them. The query must outer-join Run on GuideVersion.source_run_id."""
    return or_(GuideVersion.status == APPROVED, Run.state.not_in(UNUSABLE_DRAFT_STATES))


async def prior_guide(s: AsyncSession, project_id: uuid.UUID) -> GuideVersion | None:
    """The project guide: what the guide editor shows, what saves and checkpoint approvals are
    measured against, what a new run of the project builds on and what a run's draft is merged
    onto when it is saved (documents in one project share a guide, spec §5; CGL-23). It is the
    newest version that is approved, or an engine draft of a run that has built it and has not
    failed, been cancelled or stopped for approval. A run still translating counts, so files
    translated together share their names. Every saved draft keeps all of the project guide's
    entries (stages.guide.carry_over), so the newest is also the most complete. Nothing here
    locks an entry: locks come only from people."""
    return await s.scalar(
        select(GuideVersion)
        .outerjoin(Run, Run.id == GuideVersion.source_run_id)
        .where(GuideVersion.project_id == project_id, usable_guide_version())
        .order_by(GuideVersion.version.desc())
        .limit(1)
    )
```

- [ ] **Step 6: Implement in `engine/coglossa/catalog.py`** (in `_projects_query`, replace the `approved`, `finished` and `guide = func.coalesce(...)` lines)

```python
    guide = (  # the project guide's version (runs.prior_guide)
        select(func.max(GuideVersion.version))
        .outerjoin(Run, Run.id == GuideVersion.source_run_id)
        .where(GuideVersion.project_id == Project.id, runs.usable_guide_version())
        .scalar_subquery()
    )
```

- [ ] **Step 7: Update the web comment** (`web/src/api/guide.ts:21`)

```ts
/** The project guide (its newest approved version or in-use engine draft), or one version of it. */
```

- [ ] **Step 8: Run the three tests, then every test that reads the project guide**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/store/test_runs.py tests/test_guides.py tests/test_catalog.py tests/test_review_actions.py tests/worker/test_execute.py -q --no-cov`
Expected: all pass. `test_drafts_of_unfinished_runs_are_not_the_project_guide` (awaiting approval) and `test_a_cancelled_runs_draft_is_not_shared` still pass unchanged. If any other test asserted the old approved-first rule, update it to the new rule and say so in the commit message.

- [ ] **Step 9: Lint and commit**

```bash
cd engine && uv run ruff check . && uv run ruff format --check . && cd ../web && pnpm lint && cd ..
git add engine/coglossa/store/runs.py engine/coglossa/catalog.py web/src/api/guide.ts \
  engine/tests/store/test_runs.py engine/tests/test_guides.py engine/tests/worker/test_execute.py
git commit -m "feat(engine): the project guide is its newest approved or in-use version (CGL-23)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012TBMbjzsQyykrdRBR7Vuv5"
```

---

### Task 3: merge each draft onto the set guide when it is saved

**Files:**
- Modify: `engine/coglossa/worker/execute.py` (`_guide`, lines 269-310; the import from `coglossa.stages.guide`)
- Modify: `README.md:140-144` (the "Documents in one project share a guide" paragraph)
- Modify: `docs/superpowers/specs/2026-10-16-coglossa-ui-ux-design.md:31` (§1.3)
- Test: `engine/tests/worker/test_execute.py` (new `test_files_translated_together_agree_on_names`; replace `test_approval_is_measured_against_the_guide_a_draft_was_built_from` with two tests)

**Interfaces:**
- Consumes: `carry_over` (Task 1); `runs.prior_guide` (Task 2); existing `runs.lock_project(s, project_id)`, `runs.get_run(s, run_id, lock=True)`, `runs.add_guide_version(...)`.
- Produces: a run's saved draft is `carry_over(set guide at save time, built guide)`, with `built_from_version` = that set guide's version (or `None`).

- [ ] **Step 1: Write the failing concurrency test** (in `engine/tests/worker/test_execute.py`, in the "the project guide" section after `test_documents_in_a_project_share_the_guide_without_a_checkpoint`)

```python
def _guide_reply(rendering: str) -> str:
    return json.dumps(
        {
            "summary": "s",
            "style_guide": "g",
            "glossary": [{"term": "chat", "rendering": rendering}],
            "entities": [],
            "cultural_references": [],
        }
    )


async def test_files_translated_together_agree_on_names(
    services, tmp_path, fake_backend, monkeypatch
):
    """Two files of one set build their guides at the same moment and pick different
    renderings of "chat"; both translate with the one saved first (CGL-23)."""
    fake_backend.script("guide", _guide_reply("cat"), _guide_reply("kitty"))
    one = await submit(services, tmp_path, name="one.txt")
    two = await submit(services, tmp_path, text="Le chat mange. Il neige.", name="two.txt")
    starts = [services.queue.pop(), services.queue.pop()]
    real = execute.run_guide
    both_built = asyncio.Barrier(2)

    async def together(*args, **kwargs):
        built = await real(*args, **kwargs)
        await both_built.wait()  # neither saves before both have built from no set guide
        return built

    monkeypatch.setattr(execute, "run_guide", together)
    async with asyncio.timeout(30):  # a stage that fails before the barrier must not hang
        await asyncio.gather(*(execute.run_job(services, j.task, j.kwargs) for j in starts))
    monkeypatch.undo()
    await drain(services)
    assert await _state(services, one) is RunState.COMPLETE
    assert await _state(services, two) is RunState.COMPLETE
    guides = [await service.run_guide(services.db, r) for r in (one, two)]
    renderings = {g.rendering for guide in guides for g in guide.glossary if g.term == "chat"}
    assert len(renderings) == 1
    sent = [
        json.loads(r.messages[1].content.split("INPUT_JSON:", 1)[1])
        for r in fake_backend.requests
    ]
    used = {
        g["rendering"]
        for p in sent
        if p["task"] == "translate"
        for g in p["guide"]["glossary"]
        if g["term"] == "chat"
    }
    assert used == renderings
```

- [ ] **Step 2: Replace the approval test** (delete `test_approval_is_measured_against_the_guide_a_draft_was_built_from` and add these two in its place)

```python
async def test_a_draft_keeps_what_a_person_saved_while_it_was_built(
    services, tmp_path, monkeypatch
):
    """The guide stage reads the project guide (v2), a person saves v3 with a locked entry while
    the model works, and the draft is merged onto v3 when it is saved (v4): approving it as is
    keeps the person's entry (CGL-23)."""
    first = await submit(services, tmp_path, name="one.txt", checkpoint=True)
    await drain(services)
    assert await service.approve_guide(services.db, services.queue, first) == 2
    await drain(services)
    async with services.db.session() as s:
        project_id = (
            await runs.get_document(s, (await runs.get_run(s, first)).document_id)
        ).project_id
    locked = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    real = execute.run_guide

    async def saved_meanwhile(*args, **kwargs):
        built = await real(*args, **kwargs)
        await catalog.save_project_guide(services.db, project_id, locked, base_version=2)
        return built

    monkeypatch.setattr(execute, "run_guide", saved_meanwhile)
    second = await submit(services, tmp_path, name="two.txt", checkpoint=True)
    await drain(services)
    assert await _state(services, second) is RunState.AWAITING_GUIDE_APPROVAL
    async with services.db.session() as s:
        draft = await service.run_guide_row(s, second)
    assert (draft.version, draft.built_from_version) == (4, 3)
    assert await service.approve_guide(services.db, services.queue, second) == 5
    assert (await service.run_guide(services.db, second)).glossary == locked.glossary


async def test_approval_refuses_a_draft_older_than_an_approved_guide(services, tmp_path):
    first = await submit(services, tmp_path, name="one.txt", checkpoint=True)
    await drain(services)
    assert await service.approve_guide(services.db, services.queue, first) == 2
    await drain(services)
    second = await submit(services, tmp_path, name="two.txt", checkpoint=True)
    await drain(services)
    async with services.db.session() as s:
        draft = await service.run_guide_row(s, second)
        project_id = (
            await runs.get_document(s, (await runs.get_run(s, second)).document_id)
        ).project_id
    assert (draft.version, draft.built_from_version) == (3, 2)
    locked = Guide(
        summary="s",
        style_guide="g",
        glossary=[GlossaryEntry(term="chat", rendering="cat", locked=True)],
    )
    # a waiting draft is not the project guide, so the panel's base is still v2
    saved = await catalog.save_project_guide(services.db, project_id, locked, base_version=2)
    assert saved.info.version == 4
    with pytest.raises(service.ConflictError) as as_is:
        await service.approve_guide(services.db, services.queue, second)
    assert str(as_is.value) == (
        "the project guide changed since this draft (now version 4); reload it and approve the "
        "current guide"
    )
    assert await _state(services, second) is RunState.AWAITING_GUIDE_APPROVAL
    assert (
        await service.approve_guide(services.db, services.queue, second, locked, base_version=4)
        == 5
    )
    assert await service.run_guide(services.db, second) == locked
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/worker/test_execute.py -q --no-cov -k "agree_on_names or keeps_what_a_person or refuses_a_draft_older"`
Expected:
- `agree_on_names` fails: two renderings, `{"cat", "kitty"}`.
- `keeps_what_a_person` fails: `built_from_version` is 2, not 3.
- `refuses_a_draft_older` passes already. It pins existing behaviour that must survive the change.

- [ ] **Step 4: Implement in `engine/coglossa/worker/execute.py`**

Change the import to `from coglossa.stages.guide import carry_over, guide_was_built, run_guide`. In `_guide`, delete the line `built_from = prior_row.version if prior_row is not None else None` and replace the `save` closure with:

```python
    async def save(s: AsyncSession) -> None:
        locked = await runs.get_run(s, run_id, lock=True)  # lock order: run, then project
        await runs.lock_project(s, doc.project_id)
        # The files of one set agree on names: the draft is merged onto the project guide as it
        # is now, which may have grown while the model worked (another file's draft, a person's
        # save). Its entries win; the draft adds what is new (CGL-23).
        current = await runs.prior_guide(s, doc.project_id)
        merged = guide if current is None else carry_over(Guide.model_validate(current.content), guide)
        version = await runs.add_guide_version(
            s,
            doc.project_id,
            merged,
            status=runs.DRAFT,
            author=runs.ENGINE,
            source_run_id=run_id,
            # what a checkpoint approval is measured against
            built_from_version=current.version if current is not None else None,
        )
        locked.guide_version_id = version.id
```

(Wrap the `merged = ...` line if ruff format asks; run `uv run ruff format coglossa/worker/execute.py`.)

- [ ] **Step 5: Run the new tests, then the whole worker and guide suites**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/worker tests/test_guides.py tests/test_review_actions.py tests/test_pipeline.py -q --no-cov`
Expected: all pass.

- [ ] **Step 6: Update the README** (`README.md`, replace the paragraph that starts "Documents in one project share a guide." up to and including "`--use-project-guide` reuses the latest approved version as-is instead of building a new one."; keep the sentence about locking that follows)

```markdown
Documents in one project share a guide. The project guide is its newest version that is approved
or is the draft of a run that has built it and has not failed, been cancelled or stopped for
approval, so a run still translating counts. A new run builds its guide on the project guide,
and when the draft is saved it is merged onto the project guide as it is then: every term and
name already there keeps its entry, and the draft adds the ones that are new (it keeps its own
summary and style guide). Files of one project therefore agree on names and terms even when they
translate at the same time. `--use-project-guide` reuses the latest approved version as-is
instead of building a new one.
```

- [ ] **Step 7: Update UI spec §1.3** (`docs/superpowers/specs/2026-10-16-coglossa-ui-ux-design.md:31`, replace the whole bullet)

```markdown
- **Keeping names consistent across files while they translate in parallel.** That was an engine change, since made (CGL-23, `2026-10-17-set-guide-consistency-design.md`): each file's guide is merged onto the set's guide as it grows, so files translated together share names. Apply to all (§5.4) and the recheck still cover a person's later changes.
```

- [ ] **Step 8: Lint and commit**

```bash
cd engine && uv run ruff check . && uv run ruff format --check . && cd ..
git add engine/coglossa/worker/execute.py engine/tests/worker/test_execute.py README.md \
  docs/superpowers/specs/2026-10-16-coglossa-ui-ux-design.md
git commit -m "feat(engine): merge each draft onto the set guide when it is saved (CGL-23)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012TBMbjzsQyykrdRBR7Vuv5"
```

---

### Task 4: the final sweep follows the set guide

**Files:**
- Modify: `engine/coglossa/worker/execute.py` (`finish_run`, the `if enabled:` block around line 571)
- Test: `engine/tests/worker/test_execute.py`

**Interfaces:**
- Consumes: `carry_over` (Task 1), `runs.prior_guide` (Task 2).
- Produces: `finish_run` sweeps with `carry_over(set guide at sweep time, the run's own guide)`.

- [ ] **Step 1: Write the failing tests** (in `engine/tests/worker/test_execute.py`, after `test_a_resweep_keeps_earlier_revisions_and_uses_the_latest_approved_guide`)

```python
async def _project_of(svc, run_id):
    async with svc.db.session() as s:
        return (await runs.get_document(s, (await runs.get_run(s, run_id)).document_id)).project_id


CAT = GlossaryEntry(term="chat", rendering="cat", locked=True)


async def test_a_rule_added_while_a_file_translates_reaches_its_sweep(services, tmp_path):
    run_id = await submit(
        services, tmp_path, pipeline_profile=PipelineProfile.LONG_FORM, checkpoint=False
    )
    await execute.run_job(services, *_pop(services))  # start_run: guide built, chunks queued
    project_id = await _project_of(services, run_id)
    view = await catalog.project_guide(services.db, project_id)  # the run's draft, v1
    await catalog.save_project_guide(
        services.db,
        project_id,
        view.guide.model_copy(update={"glossary": [CAT]}),
        base_version=view.info.version,
    )
    await drain(services)
    report = await service.build_report(services.db, run_id)
    assert await _state(services, run_id) is RunState.COMPLETE
    assert [r.source for r in report.revisions] == [RevisionSource.SWEEP]
    assert report.guide.glossary == []  # the run keeps its own guide; the sweep used the set's


async def test_a_rule_removed_while_a_file_translates_is_not_enforced(
    services, tmp_path, monkeypatch
):
    project_guide = Guide(summary="s", style_guide="g", glossary=[CAT])
    first = await submit(services, tmp_path, name="one.txt", checkpoint=True)
    await drain(services)
    await service.approve_guide(services.db, services.queue, first, project_guide)
    await drain(services)
    run_id = await submit(
        services,
        tmp_path,
        text="Le chat court. Il neige.",
        name="two.txt",
        pipeline_profile=PipelineProfile.LONG_FORM,
        checkpoint=False,
    )
    await execute.run_job(services, *_pop(services))  # its guide carries the locked "chat"
    project_id = await _project_of(services, run_id)
    view = await catalog.project_guide(services.db, project_id)
    unlocked = CAT.model_copy(update={"locked": False})
    await catalog.save_project_guide(
        services.db,
        project_id,
        view.guide.model_copy(update={"glossary": [unlocked]}),
        base_version=view.info.version,
    )
    await drain(services)
    report = await service.build_report(services.db, run_id)
    assert report.revisions == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/worker/test_execute.py -q --no-cov -k "while_a_file_translates"`
Expected: both fail.
- The first has no SWEEP revision: the sweep used the run's own guide, which has no lock.
- The second has a SWEEP revision: the run's own guide still has the lock.

If the second test's run fails to reach `translating` after `run_job` (for example, `_pop` returns a `translate_run` job instead of `start_run`), print `services.queue.jobs` and pop until the `coglossa.start_run` job for `run_id` has run. Do not weaken the assertions.

- [ ] **Step 3: Implement in `finish_run`** (`engine/coglossa/worker/execute.py`; change the import to `from coglossa.stages.guide import carry_over, guide_was_built, run_guide` if Task 3 has not already, and replace the line `guide = await runs.load_guide(s, run.guide_version_id)` inside `if enabled:`)

```python
                own = await runs.load_guide(s, run.guide_version_id)
                doc = await runs.get_document(s, run.document_id)
                current = await runs.prior_guide(s, doc.project_id)
                # a rule a person added, changed or unlocked while this file translated applies
                # to it too; the run keeps its own summary and style guide (CGL-23)
                guide = (
                    own
                    if current is None
                    else carry_over(Guide.model_validate(current.content), own)
                )
```

- [ ] **Step 4: Run the new tests, then the full engine suite**

Run: `cd engine && COGLOSSA_TEST_DATABASE_URL=postgresql://coglossa:coglossa@127.0.0.1:55432/postgres uv run pytest tests/worker/test_execute.py -q --no-cov`, then from the repo root `make test-engine`.
Expected: all pass, and the coverage gate holds.

- [ ] **Step 5: Lint and commit**

```bash
cd engine && uv run ruff check . && uv run ruff format --check . && cd ..
git add engine/coglossa/worker/execute.py engine/tests/worker/test_execute.py
git commit -m "fix(engine): a file's final sweep follows the set guide as it is then (CGL-23)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012TBMbjzsQyykrdRBR7Vuv5"
```

---

### Task 5: whole-branch checks

**Files:** none changed unless a check fails.

- [ ] **Step 1:** `make lint` and `make test` from the repo root. Expected: all pass (engine, scorer, api, web).
- [ ] **Step 2:** `make e2e`. Expected: 6/6.
- [ ] **Step 3:** If any API test asserted the old project-guide rule, update it to the new rule in a commit of its own whose message names the test and the rule.
