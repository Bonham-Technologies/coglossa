"""`coglossa doctor` (spec §7.4): check an installation end to end and say how to fix what is
wrong. Every check reports pass, warn or fail with a plain-language fix."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select

from coglossa.config import (
    HTTP_BIND_ENV,
    PROXY_SECRET_HEADER,
    CoglossaConfig,
    ConfigError,
    ModelConfig,
    StructuredOutput,
    is_loopback,
    load_config,
    proxy_bypass_warning,
    published_address,
)
from coglossa.llm.agent import extract_json
from coglossa.llm.payload import render_input
from coglossa.llm.registry import build_backends
from coglossa.llm.types import (
    Backend,
    BackendError,
    ChatMessage,
    ChatRequest,
    ContextOverflowError,
    OutputTruncatedError,
)
from coglossa.people import NOBODY, PeopleFileError, load_people
from coglossa.prompts.check import validate_prompts
from coglossa.prompts.library import PromptLibrary
from coglossa.schema import CheckStatus, DoctorCheck, DoctorReport
from coglossa.scoring.client import build_scorer, probe_scorer
from coglossa.scoring.tiers import LanguageTiers
from coglossa.storage import StorageError, build_storage, describe_storage
from coglossa.store.db import Database, error_reason, redact
from coglossa.store.migrate import connect_error, migration_state
from coglossa.store.models import Project
from coglossa.worker.execute import DB_ERRORS

PING_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
_ORDER = {CheckStatus.PASS: 0, CheckStatus.WARN: 1, CheckStatus.FAIL: 2}

BackendsFactory = Callable[[CoglossaConfig], dict[str, Backend]]


def _check(name: str, status: CheckStatus, message: str, fix: str = "") -> DoctorCheck:
    return DoctorCheck(name=name, status=status, message=message, fix=fix)


def _crashed(name: str, e: Exception) -> DoctorCheck:
    return _check(name, CheckStatus.FAIL, f"internal error: {type(e).__name__}: {e}")


def _guard(name: str, fn: Callable[[], DoctorCheck]) -> DoctorCheck:
    """A diagnostic reports every check: an unexpected error becomes that check's failure."""
    try:
        return fn()
    except Exception as e:
        return _crashed(name, e)


async def _aguard(name: str, fn: Callable[[], Awaitable[DoctorCheck]]) -> DoctorCheck:
    try:
        return await fn()
    except Exception as e:
        return _crashed(name, e)


async def run_doctor(
    config_path: Path,
    *,
    probe_context: bool = True,
    backends_factory: BackendsFactory = build_backends,
) -> DoctorReport:
    try:
        cfg, issues = load_config(config_path)
    except ConfigError as e:
        return DoctorReport.of(
            [_check("config", CheckStatus.FAIL, str(e), "edit the file; see coglossa.example.yaml")]
        )
    checks = [_check("config", CheckStatus.PASS, f"{config_path} is valid")]
    checks += [_check("config", CheckStatus.WARN, i.message) for i in issues]
    checks.append(_guard("prompts", lambda: _prompts(cfg)))
    checks.append(_guard("tiers", lambda: _tiers(cfg)))
    if cfg.auth.mode == "trusted_header":
        checks.append(_guard("auth", lambda: _auth(cfg)))
        checks.append(_guard("teams", lambda: _teams(cfg)))
    checks += await _backends(cfg, probe_context, backends_factory)
    checks.append(await _aguard("scorer", lambda: _scorer(cfg)))
    if cfg.worker.kick_url:
        checks.append(
            _check("worker", CheckStatus.PASS, f"kicks {cfg.worker.kick_url} when a job is queued")
        )
    checks.append(await _aguard("database", lambda: _database(cfg)))
    checks.append(_guard("storage", lambda: _storage(cfg)))
    if all(c.status is CheckStatus.PASS for c in checks[-2:]):
        checks.append(await _aguard("leftover files", lambda: _leftover_files(cfg)))
    return DoctorReport.of(checks)


def _prompts(cfg: CoglossaConfig) -> DoctorCheck:
    try:
        validate_prompts(cfg, PromptLibrary(cfg.prompts_dir))
    except ConfigError as e:
        return _check(
            "prompts", CheckStatus.FAIL, str(e), "fix or remove the override in prompts_dir"
        )
    return _check("prompts", CheckStatus.PASS, "every prompt renders")


def _tiers(cfg: CoglossaConfig) -> DoctorCheck:
    try:
        LanguageTiers.load(cfg.scoring.language_tiers_file)
    except ConfigError as e:
        return _check("tiers", CheckStatus.FAIL, str(e), "fix scoring.language_tiers_file")
    return _check("tiers", CheckStatus.PASS, "language tiers load")


def _auth(cfg: CoglossaConfig) -> DoctorCheck:
    """CGL-61. Doctor does not know the address the API binds, so it goes by where compose
    publishes it (COGLOSSA_HTTP_BIND, passed into the containers); unset means 0.0.0.0."""
    if cfg.auth.proxy_secret_env:
        return _check(
            "auth",
            CheckStatus.PASS,
            f"named users; requests without the proxy's secret ({PROXY_SECRET_HEADER}) are refused",
        )
    address = published_address()
    if is_loopback(address):
        return _check(
            "auth",
            CheckStatus.PASS,
            f"named users; the app is published on {address} only ({HTTP_BIND_ENV}), so only "
            "a proxy on this machine reaches it",
        )
    message, fix = proxy_bypass_warning(address)
    return _check("auth", CheckStatus.WARN, message, fix)


def _teams(cfg: CoglossaConfig) -> DoctorCheck:
    """CGL-74: which groups become teams, and the people file's teams that the filter leaves
    out (doctor cannot know the proxy's groups, but the people file it can read)."""
    auth = cfg.auth
    if not auth.team_filter().active:
        if auth.groups_header is None:
            message = "teams come from the people file only (auth.groups_header is unset)"
        else:
            message = (
                "every group the proxy sends becomes a team (auth.team_groups_prefix and"
                " auth.team_groups are unset)"
            )
        return _check("teams", CheckStatus.PASS, message)
    rule = auth.team_rule()
    if rule is None:
        why = (
            "lists only auth.admin_group, which is never a team" if auth.team_groups else "is empty"
        )
        return _check(
            "teams",
            CheckStatus.WARN,
            f"auth.team_groups {why}, so no group becomes a team",
            "list the groups that are teams under auth.team_groups, or set auth.team_groups_prefix",
        )
    message = f"only groups whose names {rule} become teams"
    try:
        people = load_people(auth.people_file)
    except PeopleFileError:
        people = NOBODY  # the config check reports the file itself
    named = people.all_teams()
    left_out = [t for t in named if not auth.team_filter().allows(t)]
    if not left_out:
        return _check("teams", CheckStatus.PASS, message)
    listed = ", ".join(f"'{t}'" for t in left_out)
    if len(left_out) == len(named):
        message += f", so every team in the people file ({listed}) gives its members nothing"
    else:
        message += f", so the people file's teams {listed} give their members nothing"
    fix = (
        "rename those teams in the people file to match, add them to auth.team_groups, or"
        " remove them"
    )
    return _check("teams", CheckStatus.WARN, message, fix)


async def _backends(
    cfg: CoglossaConfig, probe: bool, factory: BackendsFactory
) -> list[DoctorCheck]:
    try:
        backends = factory(cfg)
    except ConfigError as e:
        return [_check("backends", CheckStatus.FAIL, str(e), "fix the backend kind")]
    except Exception as e:
        return [_crashed("backends", e)]
    checks: list[DoctorCheck] = []
    try:
        for name, backend in backends.items():
            checks += await _backend(cfg, name, backend, probe)
    finally:
        for backend in backends.values():
            await backend.aclose()
    return checks


async def _backend(
    cfg: CoglossaConfig, name: str, backend: Backend, probe: bool
) -> list[DoctorCheck]:
    label = f"backend {name}"
    base_url = cfg.backends[name].base_url
    try:
        served = await backend.list_models()
    except Exception as e:
        if not isinstance(e, BackendError):
            return [_crashed(label, e)]
        return [
            _check(
                label,
                CheckStatus.FAIL,
                f"cannot list models at {base_url}: {e}",
                "check base_url, that the server is running, and network access from this host",
            )
        ]
    shown = ", ".join(served[:5]) + (", …" if len(served) > 5 else "")
    checks = [_check(label, CheckStatus.PASS, f"reachable at {base_url}; serves {shown}")]
    for key, model in cfg.models.items():
        if model.backend == name:
            checks.append(
                await _aguard(
                    f"model {key}", lambda k=key, m=model: _model(k, m, backend, served, probe)
                )
            )
    return checks


def _ping(
    model_id: str, mode: StructuredOutput, extra_body: dict[str, Any] | None = None
) -> ChatRequest:
    return ChatRequest(
        model=model_id,
        messages=[
            ChatMessage(role="system", content='Reply with the JSON object {"ok": true}.'),
            ChatMessage(role="user", content=render_input({"task": "ping"})),
        ],
        schema_name="Ping",
        json_schema=PING_SCHEMA,
        structured_output=mode,
        extra_body=extra_body or {},
        temperature=0,
        max_tokens=50,
    )


async def _model(
    key: str, model: ModelConfig, backend: Backend, served: list[str], probe: bool
) -> DoctorCheck:
    label = f"model {key}"
    model_id = model.name or key
    if model_id not in served:
        return _check(
            label,
            CheckStatus.FAIL,
            f"'{model_id}' is not served by backend {model.backend}",
            f"set models.{key}.name to one of: {', '.join(served)}",
        )
    parts: list[DoctorCheck] = []
    try:
        await backend.chat(_ping(model_id, StructuredOutput.NONE, model.extra_body))
        parts.append(_check(label, CheckStatus.PASS, "test completion succeeded"))
    except OutputTruncatedError:  # cut at max_tokens: the model answered
        parts.append(_check(label, CheckStatus.PASS, "test completion succeeded"))
    except BackendError as e:
        parts.append(
            _check(
                label,
                CheckStatus.FAIL,
                f"test completion failed: {e}",
                "check the model id and server logs",
            )
        )
    if model.structured_output is not StructuredOutput.NONE and parts[0].status is CheckStatus.PASS:
        parts.append(await _structured(label, key, model, model_id, backend))
    if probe:
        parts.append(await _context(label, key, model, model_id, backend))
    worst = max(parts, key=lambda c: _ORDER[c.status])
    fix = next((c.fix for c in parts if c.status is not CheckStatus.PASS), "")
    return _check(label, worst.status, "; ".join(c.message for c in parts), fix)


async def _structured(
    label: str, key: str, model: ModelConfig, model_id: str, backend: Backend
) -> DoctorCheck:
    fix = f"set models.{key}.structured_output to json_object or none"
    try:
        reply = await backend.chat(_ping(model_id, model.structured_output, model.extra_body))
        data = json.loads(extract_json(reply.content))
    except BackendError as e:
        return _check(
            label, CheckStatus.FAIL, f"{model.structured_output.value} request failed: {e}", fix
        )
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
        return _check(
            label,
            CheckStatus.FAIL,
            f"the server did not return the requested JSON ({model.structured_output.value})",
            fix,
        )
    return _check(label, CheckStatus.PASS, f"{model.structured_output.value} output works")


async def _context(
    label: str, key: str, model: ModelConfig, model_id: str, backend: Backend
) -> DoctorCheck:
    n = int(model.context_window * 0.9)
    request = ChatRequest(
        model=model_id,
        messages=[
            ChatMessage(role="system", content="Reply with OK."),
            ChatMessage(role="user", content=render_input({"task": "probe", "filler": "a " * n})),
        ],
        schema_name="Ping",
        json_schema=PING_SCHEMA,
        structured_output=StructuredOutput.NONE,
        temperature=0,
        max_tokens=1,
        extra_body=model.extra_body,
    )
    try:
        await backend.chat(request)
    except OutputTruncatedError:
        pass  # max_tokens=1: the prompt was accepted
    except ContextOverflowError:
        return _check(
            label,
            CheckStatus.FAIL,
            f"the server rejected a prompt of about {n} tokens; context_window "
            f"{model.context_window} is larger than it accepts",
            f"lower models.{key}.context_window",
        )
    except BackendError as e:
        return _check(label, CheckStatus.WARN, f"context probe inconclusive: {e}")
    return _check(label, CheckStatus.PASS, f"accepted a prompt of about {n} tokens")


async def _scorer(cfg: CoglossaConfig) -> DoctorCheck:
    if cfg.scorer is None or cfg.scorer.kind == "none":
        return _check(
            "scorer",
            CheckStatus.WARN,
            "no scorer configured: qe and xsim signals will be empty",
            "add a scorer section pointing at coglossa-scorer",
        )
    try:
        scorer = build_scorer(cfg)
    except ConfigError as e:
        return _check("scorer", CheckStatus.FAIL, str(e), "fix the scorer section")
    assert scorer is not None
    try:
        health, warnings = await probe_scorer(scorer, cfg)
    finally:
        await scorer.aclose()
    if health is None:
        return _check(
            "scorer", CheckStatus.FAIL, warnings[0], "start coglossa-scorer or fix scorer.url"
        )
    if warnings:
        return _check("scorer", CheckStatus.WARN, "; ".join(warnings), "load the missing model")
    return _check(
        "scorer",
        CheckStatus.PASS,
        f"qe {health.qe_model}, embeddings {health.embed_model}, device {health.device}",
    )


async def _database(cfg: CoglossaConfig) -> DoctorCheck:
    url = cfg.database.url()
    env = cfg.database.url_env
    if url is None:
        return _check(
            "database",
            CheckStatus.WARN,
            f"{env} is not set: needed by coglossa worker and coglossa submit, not by coglossa run",
            f"set {env}=postgresql://user:password@host:5432/coglossa",
        )
    try:
        state = await asyncio.to_thread(migration_state, url)
    except ConfigError as e:
        return _check("database", CheckStatus.FAIL, str(e), f"fix {env}")
    except DB_ERRORS as e:
        return _check(
            "database",
            CheckStatus.FAIL,
            str(connect_error(url, e)),
            "check the URL, the credentials and that Postgres is running",
        )
    if not state.up_to_date:
        return _check(
            "database",
            CheckStatus.FAIL,
            f"schema at revision {state.current or 'none'}, expected {state.head}",
            "run coglossa db upgrade",
        )
    return _check("database", CheckStatus.PASS, f"{redact(url)} at revision {state.head}")


def _storage(cfg: CoglossaConfig) -> DoctorCheck:
    where = describe_storage(cfg.storage)
    fix = (
        "fix storage (bucket, endpoint_url, keys)"
        if cfg.storage.kind == "s3"
        else "fix storage.path or its permissions"
    )
    try:
        build_storage(cfg.storage).check_writable()
    except StorageError as e:
        return _check("storage", CheckStatus.FAIL, str(e), fix)
    return _check("storage", CheckStatus.PASS, f"{where} is writable")


_SHOWN_LEFTOVERS = 10


async def _leftover_files(cfg: CoglossaConfig) -> DoctorCheck:
    """CGL-84: a set whose files could not be removed when it was deleted leaves a folder under
    projects/ that no set owns. Listed only: doctor never deletes anything."""
    url = cfg.database.url()
    assert url is not None  # run only after the database check passed
    try:
        # storage first: a set created in between is then in the database's list
        names = await asyncio.to_thread(build_storage(cfg.storage).list_prefixes, "projects/")
        db = Database(url)
        try:
            async with db.session() as s:
                owned = {str(i) for i in await s.scalars(select(Project.id))}
        finally:
            await db.dispose()
    except StorageError as e:
        return _check("leftover files", CheckStatus.WARN, f"cannot look for leftover files: {e}")
    except DB_ERRORS as e:
        reason = error_reason(e)
        return _check(
            "leftover files", CheckStatus.WARN, f"cannot look for leftover files: {reason}"
        )
    left = [f"projects/{n}/" for n in names if n not in owned]
    if not left:
        return _check("leftover files", CheckStatus.PASS, "every folder under projects/ has a set")
    shown = ", ".join(left[:_SHOWN_LEFTOVERS]) + (", …" if len(left) > _SHOWN_LEFTOVERS else "")
    return _check(
        "leftover files",
        CheckStatus.WARN,
        f"{len(left)} folder(s) in {describe_storage(cfg.storage)} belong to no set, "
        f"probably left when deleting a set could not remove its files: {shown}",
        "check the coglossa-api logs for why, then remove these folders by hand; doctor never "
        "deletes them (a set being added or deleted right now can show here for a moment)",
    )
