"""Projects, documents and runs in the shapes the REST API returns (spec §8.1, §8.2). Reads use
one short session each; writes take the locks the run lifecycle uses."""

import asyncio
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from coglossa import access, service
from coglossa.access import EVERYONE, Viewer
from coglossa.glossary_import import (
    ImportStatus,
    ParsedGlossary,
    glossary_format,
    merge_import,
    parse_glossary,
    plan_import,
)
from coglossa.people import People, TeamFilter, nfc, team_key
from coglossa.problems import run_problems
from coglossa.schema import (
    DocumentProfile,
    FlagStatus,
    Guide,
    PipelineProfile,
    Reliability,
    RunState,
)
from coglossa.schema.views import (
    DocumentRun,
    DocumentSummary,
    GlossaryImportPreview,
    GlossaryImportResult,
    GlossaryImportTerm,
    GuideVersionInfo,
    GuideView,
    ProjectInfo,
    RunDetail,
    RunSummary,
    SetLanguage,
)
from coglossa.scoring.aggregate import document_notices, unread_notices
from coglossa.store import review as review_store
from coglossa.store import runs
from coglossa.store.db import Database
from coglossa.store.models import Document, FlagRow, GuideVersion, Project, Run, SpanRow
from coglossa.text import clean_guide, clean_text

MAX_PROJECT_NAME = 200
_REPORTABLE = frozenset({RunState.COMPLETE, RunState.COMPLETE_WITH_ERRORS})


def _projects_query() -> Select[tuple[Project, int, int]]:
    documents = (
        select(func.count(Document.id)).where(Document.project_id == Project.id).scalar_subquery()
    )
    run_count = (
        select(func.count(Run.id))
        .join(Document, Run.document_id == Document.id)
        .where(Document.project_id == Project.id)
        .scalar_subquery()
    )
    return select(Project, documents, run_count)


async def _set_languages(
    s: AsyncSession, projects: list[Project]
) -> dict[uuid.UUID, list[SetLanguage]]:
    """Two grouped queries for the whole list: the languages of the runs, and each language's
    set guide version (runs.prior_guide's SQL mirror)."""
    ids = [p.id for p in projects]
    if not ids:
        return {}
    first = func.min(Run.created_at)
    used: dict[uuid.UUID, list[str]] = defaultdict(list)
    for project_id, lang, _ in await s.execute(
        select(Document.project_id, Run.target_lang, first)
        .join(Run, Run.document_id == Document.id)
        .where(Document.project_id.in_(ids))
        .group_by(Document.project_id, Run.target_lang)
        .order_by(Document.project_id, first, Run.target_lang)
    ):
        used[project_id].append(lang)
    versions = {
        (project_id, lang): version
        for project_id, lang, version in await s.execute(
            select(
                GuideVersion.project_id, GuideVersion.target_lang, func.max(GuideVersion.version)
            )
            .outerjoin(Run, Run.id == GuideVersion.source_run_id)
            .where(GuideVersion.project_id.in_(ids), runs.usable_guide_version())
            .group_by(GuideVersion.project_id, GuideVersion.target_lang)
        )
    }
    return {
        p.id: [
            SetLanguage(target_lang=lang, guide_version=versions.get((p.id, lang)))
            for lang in runs.ordered_languages(p.target_lang, used[p.id])
        ]
        for p in projects
    }


_STOPPED = frozenset({RunState.FAILED, RunState.CANCELLED})
_FINISHED = frozenset({RunState.COMPLETE, RunState.COMPLETE_WITH_ERRORS})


@dataclass(frozen=True)
class _SetSummary:
    """What the web app's sidebar shows for a project (UI spec §8.1)."""

    last_activity_at: datetime | None = None
    translating: int = 0
    ready: int = 0
    failed: int = 0
    open_flags: int = 0


async def _set_summaries(
    s: AsyncSession, project_ids: list[uuid.UUID]
) -> dict[uuid.UUID, _SetSummary]:
    """Three grouped queries for the whole list, never one per project."""
    if not project_ids:
        return {}
    rank = func.row_number().over(
        partition_by=(Run.document_id, Run.target_lang),
        order_by=(Run.created_at.desc(), Run.id.desc()),
    )
    ranked = (
        select(Run.id, Run.state, Document.project_id, rank.label("rank"))
        .join(Document, Run.document_id == Document.id)
        .where(Document.project_id.in_(project_ids))
        .subquery()
    )
    newest = (
        await s.execute(
            select(ranked.c.id, ranked.c.state, ranked.c.project_id).where(ranked.c.rank == 1)
        )
    ).all()
    open_by_run: dict[uuid.UUID, int] = {}
    if newest:
        open_by_run = dict(
            (
                await s.execute(
                    select(SpanRow.run_id, func.count(FlagRow.id))
                    .join(FlagRow, FlagRow.span_id == SpanRow.id)
                    .where(
                        SpanRow.run_id.in_([row.id for row in newest]),
                        FlagRow.status == FlagStatus.OPEN.value,
                    )
                    .group_by(SpanRow.run_id)
                )
            ).all()
        )
    latest = func.greatest(Run.created_at, func.coalesce(Run.finished_at, Run.created_at))
    activity = dict(
        (
            await s.execute(
                select(Document.project_id, func.max(latest))
                .join(Run, Run.document_id == Document.id)
                .where(Document.project_id.in_(project_ids))
                .group_by(Document.project_id)
            )
        ).all()
    )
    counts: dict[uuid.UUID, dict[str, int]] = defaultdict(
        lambda: {"translating": 0, "ready": 0, "failed": 0, "open_flags": 0}
    )
    for row in newest:
        c = counts[row.project_id]
        # compared as strings: a state a newer version wrote is not in RunState, and counts as
        # in progress rather than as ready
        if row.state in _STOPPED:
            c["failed"] += 1
        elif row.state in _FINISHED:
            c["ready"] += 1
        else:
            c["translating"] += 1
        c["open_flags"] += open_by_run.get(row.id, 0)
    return {
        pid: _SetSummary(last_activity_at=activity.get(pid), **counts[pid]) for pid in project_ids
    }


def _project_info(
    project: Project,
    documents: int,
    run_count: int,
    languages: list[SetLanguage],
    summary: _SetSummary,
) -> ProjectInfo:
    activity = summary.last_activity_at
    return ProjectInfo(
        id=str(project.id),
        name=project.name,
        target_lang=project.target_lang,
        source_lang=project.source_lang,
        pipeline_profile=PipelineProfile(project.pipeline_profile)
        if project.pipeline_profile
        else None,
        team=project.team,
        created_by=project.created_by,
        created_at=project.created_at,
        documents=documents,
        runs=run_count,
        guide_version=languages[0].guide_version if languages else None,
        languages=languages,
        last_activity_at=max(project.created_at, activity) if activity else project.created_at,
        files_translating=summary.translating,
        files_ready=summary.ready,
        files_failed=summary.failed,
        open_flags=summary.open_flags,
    )


def _clean_name(name: str) -> str:
    name = clean_text(name).strip()
    if not name:
        raise service.InvalidRequestError("a project needs a name")
    if len(name) > MAX_PROJECT_NAME:
        raise service.InvalidRequestError(
            f"project names are at most {MAX_PROJECT_NAME} characters"
        )
    return name


async def create_project(
    db: Database,
    name: str,
    *,
    target_lang: str,
    source_lang: str | None = None,
    pipeline_profile: PipelineProfile | None = None,
    team: str | None = None,
    created_by: str | None = None,
) -> ProjectInfo:
    name = _clean_name(name)
    project = Project(
        name=name,
        target_lang=target_lang,
        source_lang=source_lang,
        pipeline_profile=pipeline_profile.value if pipeline_profile else None,
        team=team,
        created_by=None if created_by is None else nfc(created_by),  # access._sql_key
    )
    try:
        async with db.session() as s, s.begin():
            s.add(project)
            await s.flush()
    except IntegrityError as e:  # the name is unique
        raise service.ConflictError(f"a project named '{name}' already exists") from e
    return await get_project(db, project.id)


async def rename_project(db: Database, project_id: uuid.UUID, name: str) -> ProjectInfo:
    return await update_project(db, project_id, name=name)


async def set_team(db: Database, project_id: uuid.UUID, team: str | None) -> ProjectInfo:
    """CGL-26: an admin moves a set into a team (None: personal to its creator)."""
    return await update_project(db, project_id, team=team, move=True)


async def update_project(
    db: Database,
    project_id: uuid.UUID,
    *,
    name: str | None = None,
    team: str | None = None,
    move: bool = False,
) -> ProjectInfo:
    """Renames the set (`name`) and/or, with `move`, sets its team, in one transaction: a taken
    name (409) or a blank one (422) changes nothing (CGL-31)."""
    if name is not None:
        name = _clean_name(name)
    try:
        async with db.session() as s, s.begin():
            project = await runs.lock_project(s, project_id)
            if move:
                project.team = team
            if name is not None:
                project.name = name
    except IntegrityError as e:  # the name is unique
        if name is None:
            raise
        raise service.ConflictError(f"a project named '{name}' already exists") from e
    return await get_project(db, project_id)


async def known_teams(db: Database, people: People, viewer: Viewer, teams: TeamFilter) -> list[str]:
    """Teams to suggest when moving a set: the people file's, those of existing sets and the
    viewer's own, one spelling each; only those `teams` allows (CGL-74), so a set already in
    a broad group's team does not bring that group back into the picker."""
    async with db.session() as s:
        on_sets = list(  # the earliest set's spelling of a team wins
            await s.scalars(
                select(Project.team)
                .where(Project.team.is_not(None))
                .order_by(Project.created_at, Project.id)
            )
        )
    names: dict[str, str] = {}
    for team in [*people.all_teams(), *on_sets, *viewer.team_names]:
        if teams.allows(team):
            names.setdefault(team_key(team), team)
    return sorted(names.values(), key=team_key)


async def list_projects(db: Database, viewer: Viewer = EVERYONE) -> list[ProjectInfo]:
    """The sets `viewer` may see (CGL-26); everyone's for the CLI."""
    async with db.session() as s:
        query = _projects_query().where(access.visible(viewer)).order_by(Project.name)
        rows = (await s.execute(query)).all()
        summaries = await _set_summaries(s, [row[0].id for row in rows])
        languages = await _set_languages(s, [row[0] for row in rows])
    return [_project_info(*row, languages[row[0].id], summaries[row[0].id]) for row in rows]


async def get_project(db: Database, project_id: uuid.UUID) -> ProjectInfo:
    async with db.session() as s:
        row = (await s.execute(_projects_query().where(Project.id == project_id))).one_or_none()
        if row is None:
            raise runs.NotFoundError(f"project {project_id} not found")
        summaries = await _set_summaries(s, [project_id])
        languages = await _set_languages(s, [row[0]])
    return _project_info(*row, languages[project_id], summaries[project_id])


async def document_project(db: Database, document_id: uuid.UUID) -> ProjectInfo:
    """The project a document belongs to (its defaults apply to a new run of it)."""
    async with db.session() as s:
        doc = await runs.get_document(s, document_id)
    return await get_project(db, doc.project_id)


# A run summary reads neither column; the polled list reads skip them (CGL-101). raiseload
# turns a stray read into an error rather than a lazy load the async session cannot run.
_SUMMARY_ONLY = (
    defer(Run.config_snapshot, raiseload=True),
    defer(Run.prompt_versions, raiseload=True),
)


def run_summary(run: Run, doc: Document) -> RunSummary:
    profile = run.profile or {}
    chosen = profile.get("pipeline_profile") or run.pipeline_profile
    return RunSummary(
        id=str(run.id),
        project_id=str(doc.project_id),
        document_id=str(doc.id),
        filename=doc.filename,
        state=RunState(run.state),
        target_lang=run.target_lang,
        source_lang=profile.get("source_lang") or run.source_lang,
        pipeline_profile=PipelineProfile(chosen) if chosen else None,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
    )


async def list_documents(db: Database, project_id: uuid.UUID) -> list[DocumentSummary]:
    async with db.session() as s:
        await runs.get_project(s, project_id)
        docs = list(
            await s.scalars(
                select(Document)
                .where(Document.project_id == project_id)
                .order_by(Document.created_at.desc(), Document.id)
            )
        )
        run_rows = await s.scalars(
            select(Run)
            .options(*_SUMMARY_ONLY)
            .where(Run.document_id.in_([d.id for d in docs]))
            .order_by(Run.created_at.desc(), Run.id)
        )
        by_document: dict[uuid.UUID, list[Run]] = defaultdict(list)
        for run in run_rows:
            by_document[run.document_id].append(run)
        to_check = await review_store.to_check_counts(
            s, [run.id for listed in by_document.values() for run in listed]
        )
    return [
        DocumentSummary(
            id=str(d.id),
            project_id=str(d.project_id),
            filename=d.filename,
            format=d.format,
            created_at=d.created_at,
            ingested=d.ingested_at is not None,
            runs=[
                DocumentRun(**dict(run_summary(r, d)), to_check=to_check.get(r.id, 0))
                for r in by_document[d.id]
            ],
        )
        for d in docs
    ]


async def list_runs(
    db: Database,
    *,
    project_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    limit: int = 100,
    viewer: Viewer = EVERYONE,
) -> list[RunSummary]:
    query = (
        select(Run, Document)
        .options(*_SUMMARY_ONLY)
        .join(Document, Run.document_id == Document.id)
        .join(Project, Project.id == Document.project_id)
        .where(access.visible(viewer))
    )
    if project_id is not None:
        query = query.where(Document.project_id == project_id)
    if document_id is not None:
        query = query.where(Run.document_id == document_id)
    async with db.session() as s:
        rows = (await s.execute(query.order_by(Run.created_at.desc(), Run.id).limit(limit))).all()
    return [run_summary(run, doc) for run, doc in rows]


async def run_summaries(db: Database, run_ids: list[uuid.UUID]) -> list[RunSummary]:
    """Several runs' summaries in one query, in the order asked; a run that does not exist is
    left out."""
    async with db.session() as s:
        rows = (
            await s.execute(
                select(Run, Document)
                .join(Document, Run.document_id == Document.id)
                .where(Run.id.in_(run_ids))
            )
        ).all()
    found = {run.id: run_summary(run, doc) for run, doc in rows}
    return [found[run_id] for run_id in run_ids if run_id in found]


async def run_detail(db: Database, run_id: uuid.UUID) -> RunDetail:
    async with db.session() as s:
        run = await runs.get_run(s, run_id)
        doc = await runs.get_document(s, run.document_id)
        guide = (
            await runs.get_guide_version(s, run.guide_version_id) if run.guide_version_id else None
        )
        stats = await review_store.review_stats(s, run_id)
        scored = await review_store.has_scores(s, run_id)
        rows = await runs.stage_rows(s, run_id)
        problems = run_problems(service.stage_errors(rows))
        unread = service.ingest_notices(rows)
    progress = await service.run_progress(db, run_id)
    profile = DocumentProfile.model_validate(run.profile) if run.profile else None
    tier = Reliability(run.language_tier) if run.language_tier else None
    notices = (
        document_notices(
            tier, profile.source_lang, profile.target_lang, scored=scored, unread=unread
        )
        if profile is not None and tier is not None and stats.spans
        else unread_notices(unread)
    )
    return RunDetail(
        run=run_summary(run, doc),
        progress=progress,
        profile=profile,
        language_tier=tier,
        notices=notices,
        problems=problems,
        summary=Guide.model_validate(guide.content).summary if guide else None,
        guide_version=guide.version if guide else None,
        stats=stats,
        report_available=RunState(run.state) in _REPORTABLE,
        sweep_enabled=service.sweep_enabled(run),
    )


def _version_info(row: GuideVersion) -> GuideVersionInfo:
    return GuideVersionInfo(
        version=row.version,
        target_lang=row.target_lang,
        status=row.status,
        author=row.author,
        source_run_id=str(row.source_run_id) if row.source_run_id else None,
        created_at=row.created_at,
    )


def guide_view(row: GuideVersion) -> GuideView:
    return GuideView(info=_version_info(row), guide=Guide.model_validate(row.content))


async def _guide_language(
    s: AsyncSession, project_id: uuid.UUID, target_lang: str | None
) -> str | None:
    """The language a set-guide request is about: the one asked for, else the set's first
    (CGL-24). None: the set has no language yet."""
    project = await runs.get_project(s, project_id)
    if target_lang is not None:
        return await runs.set_spelling(s, project, target_lang)
    languages = await runs.set_languages(s, project)
    return languages[0] if languages else None


async def guide_versions(
    db: Database, project_id: uuid.UUID, target_lang: str | None = None
) -> list[GuideVersionInfo]:
    async with db.session() as s:
        lang = await _guide_language(s, project_id, target_lang)
        rows = await s.scalars(
            select(GuideVersion)
            .where(GuideVersion.project_id == project_id, GuideVersion.target_lang == lang)
            .order_by(GuideVersion.version.desc())
        )
        return [_version_info(r) for r in rows]


async def project_guide(
    db: Database,
    project_id: uuid.UUID,
    version: int | None = None,
    target_lang: str | None = None,
) -> GuideView:
    async with db.session() as s:
        lang = await _guide_language(s, project_id, target_lang)
        if version is None:
            row = await runs.prior_guide(s, project_id, lang) if lang is not None else None
            if row is None:
                raise runs.NotFoundError(
                    f"project {project_id} has no guide yet; it gets one when a run's guide is "
                    "approved, a run finishes or someone saves one"
                )
        else:
            row = await s.scalar(
                select(GuideVersion).where(
                    GuideVersion.project_id == project_id,
                    GuideVersion.target_lang == lang,
                    GuideVersion.version == version,
                )
            )
            if row is None:
                raise runs.NotFoundError(f"guide version {version} not found")
        return guide_view(row)


async def run_guide(db: Database, run_id: uuid.UUID) -> GuideView:
    async with db.session() as s:
        return guide_view(await service.run_guide_row(s, run_id))


async def _guide_to_edit(
    s: AsyncSession, project_id: uuid.UUID, target_lang: str | None
) -> tuple[Project, str, GuideVersion | None]:
    """The set (locked), the language and the current guide a person's change applies to."""
    project = await runs.lock_project(s, project_id)
    lang = await _guide_language(s, project_id, target_lang)
    if lang is None:
        raise _no_language(project)
    return project, lang, await runs.prior_guide(s, project_id, lang)


def _no_language(project: Project) -> service.InvalidRequestError:
    return service.InvalidRequestError(
        f"choose a target language: project '{project.name}' has none yet"
    )


def _guide_of(version: GuideVersion | None) -> Guide | None:
    return Guide.model_validate(version.content) if version else None


def _check_base(latest: GuideVersion | None, base_version: int | None) -> None:
    current = latest.version if latest else None
    if base_version != current:
        raise service.GuideChangedError(
            f"the guide changed since you opened it (now version {current or 'none'}, you "
            f"edited version {base_version or 'none'}); reload it and apply your changes again"
        )


async def save_project_guide(
    db: Database,
    project_id: uuid.UUID,
    guide: Guide,
    *,
    base_version: int | None,
    target_lang: str | None = None,
) -> GuideView:
    """A person's edit of the project guide: a new approved version. `base_version` is the
    version the edit started from; a newer save in between refuses this one."""
    guide = clean_guide(guide)
    async with db.session() as s, s.begin():
        _, lang, latest = await _guide_to_edit(s, project_id, target_lang)
        _check_base(latest, base_version)
        row = await runs.add_guide_version(
            s, project_id, guide, target_lang=lang, status=runs.APPROVED, author=runs.USER
        )
        return guide_view(row)


@dataclass(frozen=True)
class GlossaryFile:
    """A glossary file to import into a set guide (CGL-88)."""

    data: bytes
    filename: str
    source_lang: str | None = None  # the terms' language; None: the set's, else the file's


async def _documents_language(s: AsyncSession, project_id: uuid.UUID) -> str | None:
    """The language of the set's latest document whose language is known: chosen for its run,
    or detected when the run was profiled. It picks a TBX file's source language among several
    when neither the person nor the set names one."""
    rows = await s.execute(
        select(Run.source_lang, Run.profile)
        .join(Document, Run.document_id == Document.id)
        .where(Document.project_id == project_id)
        .order_by(Run.created_at.desc())
        .limit(20)  # runs still waiting for their profile come first
    )
    for chosen, profile in rows:
        found = chosen or (profile or {}).get("source_lang")
        if found:
            return str(found)
    return None


async def _read_glossary(
    db: Database, project_id: uuid.UUID, file: GlossaryFile, target_lang: str | None
) -> tuple[str, ParsedGlossary]:
    """The guide's language and the file's terms. A large file takes seconds to read: it is
    read in a worker thread, so other requests go on, and outside the set's lock, so saves and
    runs are not held up; the import then checks the guide did not change meanwhile."""
    async with db.session() as s:
        project = await runs.get_project(s, project_id)
        lang = await _guide_language(s, project_id, target_lang)
        if lang is None:
            raise _no_language(project)
        source_lang = file.source_lang or project.source_lang
        hint = None
        if source_lang is None and glossary_format(file.filename) == "tbx":
            hint = await _documents_language(s, project_id)
    parsed = await asyncio.to_thread(
        parse_glossary,
        file.data,
        file.filename,
        target_lang=lang,
        source_lang=source_lang,
        source_hint=hint,
    )
    return lang, parsed


async def preview_glossary_import(
    db: Database, project_id: uuid.UUID, file: GlossaryFile, *, target_lang: str | None = None
) -> GlossaryImportPreview:
    """What importing `file` into one language's set guide would do; writes nothing."""
    lang, parsed = await _read_glossary(db, project_id, file, target_lang)
    async with db.session() as s:
        latest = await runs.prior_guide(s, project_id, lang)
    plan = plan_import(_guide_of(latest), parsed.terms)
    counts = Counter(p.status for p in plan)
    return GlossaryImportPreview(
        format=parsed.format,
        target_lang=lang,
        source_lang=parsed.source_lang,
        base_version=latest.version if latest else None,
        new=counts[ImportStatus.NEW],
        identical=counts[ImportStatus.IDENTICAL],
        conflicts=counts[ImportStatus.CONFLICT],
        locked_conflicts=sum(p.status is ImportStatus.CONFLICT and p.current_locked for p in plan),
        duplicates=counts[ImportStatus.DUPLICATE],
        skipped_entries=parsed.skipped_entries,
        terms=[
            GlossaryImportTerm(
                line=p.term.line,
                term=p.term.term,
                rendering=p.term.rendering,
                status=p.status.value,
                current_rendering=p.current,
                current_locked=p.current_locked,
                first_line=p.first_line,
            )
            for p in plan
        ],
    )


async def import_glossary(
    db: Database,
    project_id: uuid.UUID,
    file: GlossaryFile,
    *,
    base_version: int | None,
    target_lang: str | None = None,
    lock_all: bool = False,
    overwrite_conflicts: bool = False,
) -> GlossaryImportResult:
    """Import `file` into one language's set guide as a new approved version by the user, all
    or nothing. `base_version` is the version the preview compared with; a newer one since
    refuses the import. A conflict replaces the guide's translation, a locked one's too, only
    with `overwrite_conflicts`; `lock_all` locks every imported term. With nothing to change,
    no version is added."""
    lang, parsed = await _read_glossary(db, project_id, file, target_lang)
    async with db.session() as s, s.begin():
        _, lang, latest = await _guide_to_edit(s, project_id, lang)
        _check_base(latest, base_version)
        current = _guide_of(latest)
        plan = plan_import(current, parsed.terms)
        merged, counts = merge_import(
            current, plan, lock_all=lock_all, overwrite_conflicts=overwrite_conflicts
        )
        merged = clean_guide(merged)
        row = latest
        if row is None or merged != current:
            row = await runs.add_guide_version(
                s, project_id, merged, target_lang=lang, status=runs.APPROVED, author=runs.USER
            )
        return GlossaryImportResult(
            view=guide_view(row),
            created_version=row is not latest,
            added=counts.added,
            updated=counts.updated,
            locked=counts.locked,
            kept=counts.kept,
            duplicates=sum(p.status is ImportStatus.DUPLICATE for p in plan),
        )
