# Coglossa: names and terms agree across a set's files (CGL-23)

Status: approved approach (the user chose "A, earlier renderings win", 2026-10-17); this
document is the written design for review.

## 1. Problem

A set (the engine's *project*) of several files is one run per file. Each run builds its own
translation guide from `runs.prior_guide(project)` when it reaches its guide stage. That is the
latest approved version, else the latest version whose run *finished*. When a person drops three
related files into New translation, the three runs build their guides at the same moment from
nothing, so a name shared by the files ("Jean-Baptiste", "the Assembly") can get three
renderings. Today the web app repairs this after the fact (**Always translate ▸ Apply to all**
then a recheck).

A related gap: a file still translating when **Apply to all** is used never picks up the new
rule. The recheck skips files that are not finished, and `finish_run` sweeps with the run's own
`guide_version_id`. The UI spec (§5.4 step 4.4) says such a file "picks the rule up when it
finishes"; the code does not.

## 2. Goal and success criteria

- Files of one set agree on the rendering of a name or term during the first pass, whether they
  are translated together or one after another.
- Translation stays parallel; the change adds no waiting.
- People's choices (locked entries, edits in the Translation guide panel, Always translate) win
  over the engine's.
- Earlier renderings win over later ones: once a name has a rendering in the set's guide, later
  files use it. A person changes it in the Translation guide panel.
- A file that is still translating when a rule is added follows the rule when it finishes.
- No new screens or API endpoints.

Out of scope: a set with two target languages (CGL-24); near-duplicate spellings of one name
("Jean-Baptiste" and "J.-B.") are separate entries, as today; CLI checkpoint approval racing a
parallel run of the same project (§6).

## 3. Design

### 3.1 The set guide is the newest usable version

`runs.prior_guide(project)` becomes: the guide version with the highest number that is either

- approved (by a person, or a checkpoint approval), or
- an engine draft whose run is not `failed`, `cancelled` or `awaiting_guide_approval`.

So a draft counts as soon as its run has built it and moved on, while the run is still
translating. It still never counts if the run failed or was cancelled, or while it waits for a
person to approve it. The SQL mirror in `catalog._projects_query` (the version the set list
reports) changes the same way.

Today an approved version wins over any newer draft. That rule existed because a draft could
drop or change what a person approved. §3.2 removes that possibility: every saved draft keeps
all of the set guide's entries as they were. So the newest usable version is always the most
complete one, and the approved-first rule is no longer needed.

### 3.2 A draft is merged onto the set guide when it is saved

The guide stage builds a guide as today (`run_guide(prior=set guide at start)`). When it saves,
in the same transaction and under the project row lock that `add_guide_version` already takes,
it re-reads the set guide and saves `carry_over(set_guide, draft)` instead of the bare draft:

- **glossary** (key: `term`, casefolded) and **entities** (key: `name`, casefolded): every set
  guide entry is kept exactly as it is (rendering, notes, gender, `locked`); the draft adds only
  entries whose key the set guide does not have. Order: the set guide's entries, then the new
  ones.
- **cultural_references**: the set guide's, then the draft's that are not already there.
- The draft never adds a **locked** entry the set guide lacks. Only people lock entries and
  the set guide keeps every lock, so such an entry is one a person removed while the draft was
  being built.
- **summary** and **style_guide**: the draft's own, because the summary describes this document
  and the style guide follows its register. Two exceptions:
  - A blank one (a guide that could not be built) keeps the set guide's.
  - If a person saved the set guide after the version the draft started from, their style guide
    wins, because the model never saw it.
- A checkpointed run is marked `awaiting_guide_approval` in the same transaction, so its
  unapproved draft is never the set guide, even for a moment.
- `built_from_version` is the set guide's version at save time.

The run then translates with this merged version (`run.guide_version_id` points at it). This is
what makes concurrent files agree. Three files that build their guides at the same moment
each save in turn under the project lock. The first file's renderings are in the set guide by
the time the second saves, so the second file's version, and its translation, use them. No
stage waits for another.

`carry_over` is a pure function in `stages/guide.py`, next to `apply_locked`, with
`carry_over(None, draft) == draft`.

**Change from the approach as first described:** I first proposed running a set's guide stages
one at a time, as their own queued job locked per project. The merge at save makes that
unnecessary for consistency, and without it there is no delay. The serial chain's one extra
benefit is that a later file's model would see earlier names while it writes notes and related
terms. That benefit isn't worth a few minutes per file. If near-duplicate spellings turn out to
matter, the serial job can be added later on top of this design.

### 3.3 Rules added mid-translation reach running files

`finish_run` sweeps with `carry_over(set_guide, own_guide)`, where the set guide is
`prior_guide` at sweep time. Every entry of the set guide wins, so a lock a person set while the
file was translating is enforced, and a lock they removed or a rendering they changed is
followed too. The run keeps its own summary and style guide. The run's own entries are already
in the set guide, because its draft was merged when it was saved (§3.2). `rerun_sweep` (the
recheck) is unchanged.

### 3.4 What else sees the change

These already read `prior_guide`, so they follow §3.1 with no code change:

- **the Translation guide panel** (`project_guide`): it now shows the guide as it grows during
  the first pass, not only after a file finishes.
- **a panel save** (`save_project_guide`): its base-version check now refuses a save when an
  engine draft landed after the panel was opened. Today the same refusal happens when a file
  finishes. The web shows "Someone else changed the guide. Reload…". The wording stays; a
  follow-up can change it if the engine is reported as "someone".
- **Always translate ▸ Apply to all** (`review.add_guide_entry`): it builds the new approved
  version on the newest guide, so it no longer drops entries from files still translating.
- **checkpoint approval** (`approve_guide`): its refusal rule (a newer *approved* version than
  the draft's base) is unchanged.

## 4. Error handling

- A guide that could not be built (CGL-14 `without_guide`) saves `carry_over(set guide, the
  earlier guide or an empty one)`: the set guide's entries, with that fallback's summary and
  style guide, or the set guide's where the fallback's are blank. The run then translates with
  the set's names.
- The merge is in-process code with no model call. If it raises, the stage's save fails and the
  run fails through the existing `_fail_run` path.
- A run that fails or is cancelled after its draft was saved leaves its entries in later
  versions that merged them. That is the intended outcome: they were renderings already used in
  translation.

## 5. Testing

- `carry_over`: keeps every base entry unchanged; adds only new keys; casefold keys; keeps
  locked flags from the base; cultural references deduplicated; draft summary and style guide;
  `None` base.
- `prior_guide` and the catalog query agree, case by case:
  - an approved version older than a usable draft
  - drafts of translating, sweeping and finished runs
  - excluded: drafts of failed, cancelled and awaiting runs
  - no versions at all
- Worker:
  - two runs of one project whose guide stages build different renderings for one name: the
    second saved version carries the first's rendering, and its chunks get it.
  - a third run started later builds on the newest version.
  - a lock added while a run translates is enforced by that run's sweep.
- Existing guide, review, catalog and API tests keep passing. Where one asserts the
  approved-wins-over-newer-draft rule, it is updated to the new rule, and the PR says so.

## 6. Known limits

- **Checkpoint approval racing a parallel run.** A run awaiting approval whose set gained a
  newer *draft* is approved from its own draft. Entries only in that newer draft are not in the
  approved version, and the next draft builds on the approved version. This happens only with
  CLI checkpoints and parallel runs of one project; the web app never pauses for approval.
- **Variant spellings.** Merge keys are casefolded terms and names. Spelling variants of one
  name are distinct entries (§2).

## 7. Documents to update

- `README.md`: the "Documents in one project share a guide" paragraph (drafts of runs that are
  still translating now count; the merge rule).
- `docs/superpowers/specs/2026-10-16-coglossa-ui-ux-design.md`: §1.3 (no longer out of scope)
  and §5.4 step 4.4 (now true).
- The docstring of `runs.prior_guide`.
