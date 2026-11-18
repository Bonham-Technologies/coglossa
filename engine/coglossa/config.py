"""coglossa.yaml: the single file that adapts Coglossa to a deployment (spec §3)."""

import hashlib
import ipaddress
import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    field_validator,
)

from coglossa.people import (
    TEAM_NAME_PATTERN,
    PeopleFileError,
    TeamFilter,
    load_people,
    strip_name,
    team_key,
)
from coglossa.schema import ErrorSeverity, PipelineProfile

CONFIG_ENV = "COGLOSSA_CONFIG"


def default_config_path() -> Path:
    """`--config`'s default: $COGLOSSA_CONFIG (the container images set it), else
    ./coglossa.yaml."""
    return Path(os.environ.get(CONFIG_ENV) or "coglossa.yaml")


class ConfigError(Exception):
    """Configuration is invalid; the message is safe to show to an operator."""


class StructuredOutput(StrEnum):
    JSON_SCHEMA = "json_schema"  # server enforces the schema (vLLM/Ollama/TGI guided decoding)
    JSON_OBJECT = "json_object"  # server guarantees JSON; schema is described in the prompt
    NONE = "none"  # plain text; schema described in the prompt and JSON extracted


# Request keys Coglossa sets itself: a model's extra_body may not carry them.
_EXTRA_BODY_OWN = frozenset({"model", "temperature", "max_tokens"})
_EXTRA_BODY_BARRED = frozenset({"messages", "stream", "response_format"})
RESERVED_BODY_KEYS = _EXTRA_BODY_OWN | _EXTRA_BODY_BARRED


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackendConfig(_Strict):
    kind: str = "openai_compat"
    base_url: str
    api_key_env: str | None = None
    max_concurrency: int = Field(4, ge=1)
    timeout_s: float = Field(120.0, gt=0)
    # Stream answers, so a model stuck repeating itself is stopped early (CGL-71). A server that
    # cannot stream is detected and asked without streaming; false always asks without it.
    stream: bool = True


class ModelConfig(_Strict):
    backend: str
    name: str | None = None  # model id sent to the server; defaults to the config key
    context_window: int = Field(ge=2048)
    structured_output: StructuredOutput = StructuredOutput.JSON_SCHEMA
    bytes_per_token: float = Field(2.5, gt=0)  # conservative UTF-8 bytes per token estimate
    max_output_tokens: int | None = Field(None, ge=1)
    temperature: float = Field(0.2, ge=0)
    # Server options added to every request body, e.g. chat_template_kwargs to turn thinking off.
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @field_validator("extra_body")
    @classmethod
    def _extra_body_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if key in _EXTRA_BODY_OWN:
                raise ValueError(f"'{key}' is set by Coglossa; use the model's own setting")
            if key in _EXTRA_BODY_BARRED:
                raise ValueError(f"'{key}' cannot be set")
        return value


# The judge's output limit when its role sets none (CGL-57): a batch's errors fit in far less,
# so a judge that loops stops here, not at the model's limit.
JUDGE_MAX_OUTPUT_TOKENS = 8192


def _role_output_limit() -> Any:
    # Left out of dumps when unset, so a config without it keeps its hash and snapshot.
    return Field(None, ge=1, exclude_if=lambda v: v is None)


class RoleConfig(_Strict):
    model: str
    max_output_tokens: int | None = _role_output_limit()  # unset: the model's (judge: lower)


class TranslatorConfig(_Strict):
    name: str
    prompt: str  # prompt template id, e.g. "literal", "contextual", or a custom one
    model: str
    max_output_tokens: int | None = _role_output_limit()


class RolesConfig(_Strict):
    literal_translator: RoleConfig
    contextual_translator: RoleConfig
    extra_translators: list[TranslatorConfig] = Field(default_factory=list)
    reviewer: RoleConfig
    profiler_guide: RoleConfig
    judge: RoleConfig | None = None  # used from M2
    roundtrip: RoleConfig | None = None  # used from M2

    def translators(self) -> list[TranslatorConfig]:
        return [
            TranslatorConfig(name=name, prompt=name, **role.model_dump())
            for name, role in (
                ("literal", self.literal_translator),
                ("contextual", self.contextual_translator),
            )
        ] + self.extra_translators

    def configured(self) -> list[tuple[str, RoleConfig | TranslatorConfig]]:
        """Each role in use, by its label (e.g. "translator:literal", "judge")."""
        roles: list[tuple[str, RoleConfig | TranslatorConfig]] = [
            (f"translator:{t.name}", t) for t in self.translators()
        ]
        roles += [("reviewer", self.reviewer), ("profiler_guide", self.profiler_guide)]
        if self.judge is not None:
            roles.append(("judge", self.judge))
        if self.roundtrip is not None:
            roles.append(("roundtrip", self.roundtrip))
        return roles

    def all_roles(self) -> list[tuple[str, str]]:
        return [(label, role.model) for label, role in self.configured()]


class RetryConfig(_Strict):
    max_attempts: int = Field(4, ge=1)
    base_delay_s: float = Field(2.0, ge=0)
    max_delay_s: float = Field(60.0, ge=0)


class ChunkingConfig(_Strict):
    target_words: int = Field(1500, ge=100)
    max_words: int = Field(2000, ge=200)
    reserve_tokens: int = Field(3000, ge=0)  # prompt + guide + safety margin per call


class DatabaseConfig(_Strict):
    url_env: str = "COGLOSSA_DATABASE_URL"  # env var holding postgresql://user:pass@host:port/db

    def url(self) -> str | None:
        return os.environ.get(self.url_env) or None


class StorageConfig(_Strict):
    kind: Literal["local"] = "local"  # or "s3": S3StorageConfig
    path: Path = Path("/data/files")


class S3StorageConfig(_Strict):
    """Files in an S3-compatible bucket (AWS S3, Supabase Storage, MinIO). The keys are read
    from the environment variables named here; they never go in the config file."""

    kind: Literal["s3"]
    endpoint_url: str | None = None  # None: AWS S3 itself
    region: str = "us-east-1"
    bucket: str = Field(min_length=3, max_length=63)
    prefix: str = ""  # keys go under this prefix, e.g. one bucket shared by several sites
    access_key_id_env: str = "COGLOSSA_S3_ACCESS_KEY_ID"
    secret_access_key_env: str = "COGLOSSA_S3_SECRET_ACCESS_KEY"


def _storage_kind(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("kind", "local")
    return getattr(value, "kind", "local")


AnyStorageConfig = Annotated[
    Annotated[StorageConfig, Tag("local")] | Annotated[S3StorageConfig, Tag("s3")],
    Discriminator(_storage_kind),
]


class WorkerConfig(_Strict):
    concurrency: int = Field(8, ge=1)  # chunk jobs one worker process runs at the same time
    # re-queue jobs of a worker silent this long; well above procrastinate's 10 s heartbeat
    stalled_after_s: float = Field(60.0, ge=30)
    # at startup, wait this long for a configured scorer to answer its health check (it loads
    # its models first, e.g. after a reboot); then start anyway, unscored. 0: do not wait
    scorer_wait_s: float = Field(900.0, ge=0)
    # the same wait for a one-off drain (`coglossa worker --burst`), which should not sit out a
    # stopped scorer; the hosted preview's on-demand worker sets it to cover the scorer's cold
    # start. 0: check once and start at once
    burst_scorer_wait_s: float = Field(0.0, ge=0)
    # POSTed after each queued job, so an on-demand worker starts (hosted preview); unset: an
    # always-on worker polls the queue
    kick_url: str | None = None
    kick_token_env: str | None = None  # sent as "Authorization: Bearer <value>"
    # more headers for the kick, each {header name: name of the variable holding its value},
    # e.g. a hosting platform's own access token
    extra_headers_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("kick_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("must be an http:// or https:// URL")
        return value

    @field_validator("extra_headers_env")
    @classmethod
    def _header_names(cls, value: dict[str, str]) -> dict[str, str]:
        return _check_header_names(value)


class ApiConfig(_Strict):
    max_upload_mb: int = Field(200, ge=1)  # larger uploads are refused (HTTP 413)
    progress_poll_s: float = Field(1.0, gt=0, le=60)  # how often the progress stream looks
    web_dir: Path | None = None  # the built web app (web/dist); the API serves it at /


_HTTP_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_TEAM_NAME = re.compile(TEAM_NAME_PATTERN)
_TEAM_PREFIX = re.compile(r"[A-Za-z0-9._-][A-Za-z0-9._ -]{0,63}")
_HEADER_NAME_HELP = "letters, digits and !#$%&'*+-.^_`|~, without spaces (e.g. X-Remote-User)"


def env_headers(names: dict[str, str]) -> dict[str, str]:
    """{header: variable name} to {header: the variable's value}; validate_config has made
    sure each variable is set."""
    return {header: os.environ.get(var, "") for header, var in names.items()}


def _check_header_names(value: dict[str, str]) -> dict[str, str]:
    for name in value:
        if not _HTTP_TOKEN.fullmatch(name):
            raise ValueError(f"{name!r} is not an HTTP header name: {_HEADER_NAME_HELP}")
    return value


class AuthConfig(_Strict):
    """Spec §3. `none`: anyone who reaches the API may use it. `trusted_header`: a reverse
    proxy authenticates people and passes the user name in `header`; requests without it are
    refused. v1 has no user management (spec §1.3)."""

    mode: Literal["none", "trusted_header"] = "none"
    header: str = "X-Remote-User"
    # A header in which the proxy passes the person's groups, comma-separated (CGL-26). Unset:
    # never read, even when a client sends it, so a proxy that does not set it is not bypassed.
    groups_header: str | None = None
    admin_group: str = "coglossa-admins"  # members of this group are admins (it is no team)
    # CGL-74: which groups become teams. Unset (both): every group. Otherwise a group (or a
    # people file team) is a team if its name starts with the prefix or is on the list,
    # compared as team names are (A-Z in any case). The admin group is never a team.
    team_groups_prefix: str | None = None
    team_groups: list[str] | None = None
    people_file: Path | None = None  # teams and admins (coglossa.people), relative to the config
    # A secret the authenticating proxy adds to every request (header X-Coglossa-Proxy-Secret);
    # requests without it are refused. For an API whose address is reachable without the proxy;
    # without it, coglossa-api and doctor warn when the API is (CGL-61, proxy_bypass_address).
    proxy_secret_env: str | None = None
    logout_url: str | None = None  # the web app links "Sign out" here (the proxy's own page)

    @field_validator("header", "groups_header")
    @classmethod
    def _http_token(cls, value: str | None) -> str | None:
        if value is not None and not _HTTP_TOKEN.fullmatch(value):
            raise ValueError(f"must be an HTTP header name: {_HEADER_NAME_HELP}")
        return value

    @field_validator("team_groups_prefix")
    @classmethod
    def _team_prefix(cls, value: str | None) -> str | None:
        """A prefix no team name could start with would silently leave every group out."""
        if value is not None and not _TEAM_PREFIX.fullmatch(value):
            raise ValueError(
                "must be the start of a team name: 1-64 letters, digits, spaces, '.', '_' or"
                " '-', not starting with a space (e.g. coglossa-)"
            )
        return value

    @field_validator("team_groups")
    @classmethod
    def _team_list(cls, value: list[str] | None) -> list[str] | None:
        for name in value or []:
            if not _TEAM_NAME.fullmatch(strip_name(name)):
                raise ValueError(
                    f"'{name}' is not a team name: use 1-64 letters, digits, spaces, '.', '_'"
                    " or '-'"
                )
        return value

    def team_filter(self) -> TeamFilter:
        """The groups that become teams (coglossa.people.TeamFilter); never the admin group."""
        return TeamFilter.of(self.team_groups_prefix, self.team_groups, self.admin_group)

    def team_rule(self) -> str | None:
        """What a team's name must do, to finish "groups whose names ...", as configured; None
        when no name can be a team (no prefix, and an allowlist that is empty or names only
        the admin group, which is never a team)."""
        parts = []
        if self.team_groups_prefix is not None:
            parts.append(f"start with '{self.team_groups_prefix}'")
        admin_key = team_key(self.admin_group)
        listed = [strip_name(n) for n in self.team_groups or [] if team_key(n) != admin_key]
        if listed:
            parts.append("are " + ", ".join(f"'{n}'" for n in listed))
        return " or ".join(parts) or None


PROXY_SECRET_HEADER = "X-Coglossa-Proxy-Secret"  # coglossa_api.proxy checks it
HTTP_BIND_ENV = "COGLOSSA_HTTP_BIND"  # where compose publishes the app (deploy/compose.yaml)


def is_loopback(host: str) -> bool:
    """127.0.0.0/8, ::1 or localhost (brackets allowed): only this machine can connect."""
    name = host.strip().removeprefix("[").removesuffix("]")
    if name.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


_WILDCARDS = frozenset({"", "0.0.0.0", "::", "[::]"})


def published_address(listen_host: str | None = None) -> str:
    """CGL-61: the address other machines would reach the app on. `listen_host` is the address
    the API binds, None when unknown (doctor). In a container the API binds 0.0.0.0 and what
    counts is where compose publishes the port, COGLOSSA_HTTP_BIND, which compose passes in.
    A loopback `listen_host` wins (nothing else can connect), and so does any other specific
    address (it is reachable there, whatever COGLOSSA_HTTP_BIND says); for every interface,
    COGLOSSA_HTTP_BIND, then `listen_host`, then 0.0.0.0, compose's own default."""
    if listen_host is not None and listen_host.strip() not in _WILDCARDS:
        return listen_host
    return os.environ.get(HTTP_BIND_ENV) or listen_host or "0.0.0.0"


def proxy_bypass_address(auth: AuthConfig, listen_host: str | None = None) -> str | None:
    """CGL-61: with named users and no proxy secret, anyone who reaches the app without the
    sign-in proxy can send any user name. The address they could reach it on, or None when
    they cannot: named users are off, a proxy secret is set, or it is on loopback only."""
    if auth.mode != "trusted_header" or auth.proxy_secret_env:
        return None
    address = published_address(listen_host)
    return None if is_loopback(address) else address


def proxy_bypass_warning(address: str) -> tuple[str, str]:
    """What `proxy_bypass_address` found, and the fix, for coglossa-api and doctor."""
    return (
        "named users (auth.mode: trusted_header) without auth.proxy_secret_env, and the app is "
        f"reachable on {address}: anyone who can reach it there without the sign-in proxy can "
        "claim any user name",
        f"set auth.proxy_secret_env and have the proxy send that secret in {PROXY_SECRET_HEADER}, "
        f"or set {HTTP_BIND_ENV}=127.0.0.1 in .env if the proxy runs on this machine",
    )


def _default_sweep() -> dict[PipelineProfile, bool]:
    return {
        PipelineProfile.SHORT: False,
        PipelineProfile.STANDARD: True,
        PipelineProfile.LONG_FORM: True,
    }


def _default_checkpoints() -> dict[PipelineProfile, bool]:
    return {
        PipelineProfile.SHORT: False,
        PipelineProfile.STANDARD: False,
        PipelineProfile.LONG_FORM: True,
    }


class ProfilesConfig(_Strict):
    short_max_words: int = Field(1500, ge=1)
    standard_max_words: int = Field(20000, ge=1)
    checkpoint: dict[PipelineProfile, bool] = Field(default_factory=_default_checkpoints)
    sweep: dict[PipelineProfile, bool] = Field(default_factory=_default_sweep)

    @field_validator("sweep")
    @classmethod
    def _merge_sweep(cls, value: dict[PipelineProfile, bool]) -> dict[PipelineProfile, bool]:
        return {**_default_sweep(), **value}


class AuditConfig(_Strict):
    store_llm_io: bool = True


_TIER_NAMES = ("A", "B", "C")


class ScorerConfig(_Strict):
    kind: str = "http"  # "http", "none", or a plugin kind from the coglossa.scorers group
    url: str | None = None
    timeout_s: float = Field(300.0, gt=0)
    connect_timeout_s: float = Field(10.0, gt=0)
    max_batch: int = Field(64, ge=1, le=256)
    max_concurrency: int = Field(2, ge=1)  # simultaneous requests to the scorer service
    api_key_env: str | None = None  # sent as "Authorization: Bearer <value>"
    # more headers, each {header name: name of the variable holding its value}, e.g. a
    # hosting platform's own access token in front of the scorer
    extra_headers_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("extra_headers_env")
    @classmethod
    def _header_names(cls, value: dict[str, str]) -> dict[str, str]:
        return _check_header_names(value)


class SignalWeights(_Strict):
    qe: float = Field(ge=0)
    xsim: float = Field(ge=0)
    roundtrip: float = Field(ge=0)
    judge: float = Field(ge=0)
    agreement: float = Field(ge=0)

    def total(self) -> float:
        return self.qe + self.xsim + self.roundtrip + self.judge + self.agreement


def _default_weights() -> dict[str, SignalWeights]:
    return {
        "A": SignalWeights(qe=0.35, xsim=0.10, roundtrip=0.20, judge=0.30, agreement=0.05),
        "B": SignalWeights(qe=0.30, xsim=0.10, roundtrip=0.20, judge=0.30, agreement=0.10),
        "C": SignalWeights(qe=0.10, xsim=0.05, roundtrip=0.30, judge=0.40, agreement=0.15),
    }


def _default_meaning_thresholds() -> dict[str, float]:
    # Calibrated in October 2026 (docs/bench-results-2026-10.md): each flags about one clean
    # passage in ten. The numbers differ by tier because each tier weighs the signals differently.
    return {"A": 86.0, "B": 84.0, "C": 70.0}


def _default_mqm() -> dict[ErrorSeverity, float]:
    return {ErrorSeverity.MINOR: 1.0, ErrorSeverity.MAJOR: 5.0, ErrorSeverity.CRITICAL: 10.0}


class ThresholdsConfig(_Strict):
    # a number applies to every tier; a map ({A: 70, C: 75}) overrides tiers individually
    meaning: dict[str, float] = Field(default_factory=_default_meaning_thresholds)
    style: float = Field(100.0, ge=0, le=100)  # any style error the judge finds (calibrated)

    @field_validator("meaning", mode="before")
    @classmethod
    def _uniform_meaning(cls, value: Any) -> Any:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return dict.fromkeys(_TIER_NAMES, value)
        return value

    @field_validator("meaning")
    @classmethod
    def _merge_meaning(cls, value: dict[str, float]) -> dict[str, float]:
        return {**_default_meaning_thresholds(), **value}


class ScoringConfig(_Strict):
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    weights_by_tier: dict[str, SignalWeights] = Field(default_factory=_default_weights)
    literary_roundtrip_factor: float = Field(0.5, ge=0, le=1)
    xsim_floor: float = Field(0.5, ge=-1, lt=1)
    mqm_penalties: dict[ErrorSeverity, float] = Field(default_factory=_default_mqm)
    judge_penalty_scale: float = Field(10.0, gt=0)
    length_ratio_band: tuple[float, float] = (0.5, 2.0)
    language_tiers_file: Path | None = None
    # Longer inputs are not sent (MetricX reads 1536 tokens, LaBSE 256); their signal stays null.
    qe_max_chars: int = Field(1500, ge=1)  # source + hypothesis + reference of one QE item
    sim_max_chars: int = Field(800, ge=1)  # each side of one similarity pair

    @field_validator("weights_by_tier")
    @classmethod
    def _merge_weights(cls, value: dict[str, SignalWeights]) -> dict[str, SignalWeights]:
        return {**_default_weights(), **value}

    @field_validator("mqm_penalties")
    @classmethod
    def _merge_mqm(cls, value: dict[ErrorSeverity, float]) -> dict[ErrorSeverity, float]:
        return {**_default_mqm(), **value}


class CoglossaConfig(_Strict):
    backends: dict[str, BackendConfig]
    models: dict[str, ModelConfig]
    roles: RolesConfig
    retry: RetryConfig = Field(default_factory=RetryConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    profiles: ProfilesConfig = Field(default_factory=ProfilesConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    prompts_dir: Path | None = None
    scorer: ScorerConfig | None = None
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    storage: AnyStorageConfig = Field(default_factory=StorageConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    @field_validator("scoring", "database", "storage", "worker", "api", "auth", mode="before")
    @classmethod
    def _null_section(cls, value: Any) -> Any:
        return {} if value is None else value

    def output_limit(
        self, role: RoleConfig | TranslatorConfig, default: int | None = None
    ) -> int | None:
        """The output token limit for a role's calls: its own max_output_tokens, else `default`
        (the judge's is JUDGE_MAX_OUTPUT_TOKENS), never above its model's; with neither, the
        model's. None: no limit is sent."""
        model = self.models[role.model]
        own = role.max_output_tokens
        if own is None and default is not None and model.max_output_tokens is None:
            # A model without a limit leaves the server to fill what the prompt leaves of the
            # context; a fixed default must leave the prompt room too, or a small context
            # window refuses every call.
            own = min(default, model.context_window // 2)
        limits = [n for n in (own or default, model.max_output_tokens) if n]
        return min(limits) if limits else None


class ConfigIssue(BaseModel):
    level: Literal["error", "warning"]
    message: str


def validate_config(cfg: CoglossaConfig) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []

    def add(level: Literal["error", "warning"], message: str) -> None:
        issues.append(ConfigIssue(level=level, message=message))

    for name, model in cfg.models.items():
        if model.backend not in cfg.backends:
            add("error", f"model '{name}' references unknown backend '{model.backend}'")
    for label, role in cfg.roles.configured():
        model = cfg.models.get(role.model)
        if model is None:
            add("error", f"role '{label}' references unknown model '{role.model}'")
        elif (role.max_output_tokens or 0) > (model.max_output_tokens or float("inf")):
            add(
                "error",
                f"role '{label}': max_output_tokens {role.max_output_tokens} is above model "
                f"'{role.model}''s {model.max_output_tokens}; raise "
                f"models.{role.model}.max_output_tokens or lower the role's",
            )

    seen: set[str] = set()
    for t in cfg.roles.translators():
        if t.name in seen:
            add("error", f"duplicate translator name '{t.name}'")
        seen.add(t.name)

    diversity_level: Literal["error", "warning"] = "warning" if len(cfg.models) == 1 else "error"
    if cfg.roles.judge is not None and cfg.roles.judge.model == cfg.roles.reviewer.model:
        add(
            diversity_level,
            "judge must use a different model from reviewer (scores are less reliable otherwise)",
        )
    translator_models = {t.model for t in cfg.roles.translators()}
    if cfg.roles.roundtrip is not None and cfg.roles.roundtrip.model in translator_models:
        add(
            diversity_level,
            "roundtrip must use a model that no translator uses "
            "(scores are less reliable otherwise)",
        )

    for name, backend in cfg.backends.items():
        if backend.api_key_env and os.environ.get(backend.api_key_env) is None:
            add(
                "error",
                f"backend '{name}': environment variable {backend.api_key_env} is not set",
            )

    if isinstance(cfg.storage, S3StorageConfig):
        for var in (cfg.storage.access_key_id_env, cfg.storage.secret_access_key_env):
            if not os.environ.get(var):
                add("error", f"storage: environment variable {var} is not set")

    if cfg.auth.proxy_secret_env and not os.environ.get(cfg.auth.proxy_secret_env):
        add("error", f"auth: environment variable {cfg.auth.proxy_secret_env} is not set")

    worker_vars = [cfg.worker.kick_token_env, *cfg.worker.extra_headers_env.values()]
    for var in worker_vars:
        if var and not os.environ.get(var):
            add("error", f"worker: environment variable {var} is not set")

    if cfg.scorer is not None:
        for var in [cfg.scorer.api_key_env, *cfg.scorer.extra_headers_env.values()]:
            if var and not os.environ.get(var):
                add("error", f"scorer: environment variable {var} is not set")

    if cfg.chunking.target_words > cfg.chunking.max_words:
        add("error", "chunking.target_words must not exceed chunking.max_words")

    if cfg.profiles.short_max_words > cfg.profiles.standard_max_words:
        add("error", "profiles.short_max_words must not exceed profiles.standard_max_words")

    if cfg.scorer is not None and cfg.scorer.kind == "http" and not cfg.scorer.url:
        add("error", "scorer.url is required when scorer.kind is http")
    scoring = cfg.scoring
    for tier, weights in scoring.weights_by_tier.items():
        if tier not in _TIER_NAMES:
            add("error", f"scoring.weights_by_tier: unknown tier '{tier}' (use A, B or C)")
        elif weights.total() <= 0:
            add("error", f"scoring.weights_by_tier: weights for tier {tier} sum to 0")
    for tier, threshold in scoring.thresholds.meaning.items():
        if tier not in _TIER_NAMES:
            add("error", f"scoring.thresholds.meaning: unknown tier '{tier}' (use A, B or C)")
        elif not 0 <= threshold <= 100:
            add("error", f"scoring.thresholds.meaning.{tier} must be between 0 and 100")
    low, high = scoring.length_ratio_band
    if not 0 < low < high:
        add("error", "scoring.length_ratio_band must be [low, high] with 0 < low < high")
    if any(p < 0 for p in scoring.mqm_penalties.values()):
        add("error", "scoring.mqm_penalties must not be negative")
    admin_key = team_key(cfg.auth.admin_group)
    for name in cfg.auth.team_groups or []:
        if team_key(name) == admin_key:  # access.viewer_for leaves it out (CGL-31, CGL-74)
            add(
                "warning",
                f"auth.team_groups lists '{name}', the name of auth.admin_group: that group"
                " makes its members admins and is never a team",
            )
    if cfg.auth.people_file is not None:
        try:
            people = load_people(cfg.auth.people_file)
        except PeopleFileError as e:
            add("error", str(e))
        else:
            for team in people.all_teams():
                if team_key(team) == admin_key:  # access.viewer_for leaves it out (CGL-31)
                    add(
                        "warning",
                        f"people file: team '{team}' has the name of auth.admin_group, so it"
                        " gives its members nothing: list admins under 'admins'",
                    )
    if scoring.language_tiers_file is not None and not scoring.language_tiers_file.is_file():
        add("error", f"language tiers file not found: {scoring.language_tiers_file}")
    return issues


def _first_error(e: ValidationError) -> str:
    """One line for an operator (spec: every message is one line): the first problem with its
    location, and how many more there are."""
    errors = e.errors()
    first = errors[0]
    where = ".".join(str(part) for part in first["loc"]) or "(top level)"
    return f"{where}: {first['msg']}{_more(len(errors) - 1)}"


def _more(count: int) -> str:
    return f" (and {count} more problem{'s' if count > 1 else ''})" if count else ""


def load_config(path: Path) -> tuple[CoglossaConfig, list[ConfigIssue]]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {' '.join(str(e).split())}") from e
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"cannot read config file {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping of sections")
    try:
        cfg = CoglossaConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"{path}: {_first_error(e)}") from e
    # Relative paths are relative to the config file. Resolving them makes the config hash
    # independent of how the config path was spelled (relative, absolute, via a symlink).
    base = path.resolve().parent
    if cfg.prompts_dir is not None:
        cfg = cfg.model_copy(update={"prompts_dir": (base / cfg.prompts_dir).resolve()})
    if cfg.api.web_dir is not None:
        api = cfg.api.model_copy(update={"web_dir": (base / cfg.api.web_dir).resolve()})
        cfg = cfg.model_copy(update={"api": api})
    tiers_file = cfg.scoring.language_tiers_file
    if tiers_file is not None:
        scoring = cfg.scoring.model_copy(
            update={"language_tiers_file": (base / tiers_file).resolve()}
        )
        cfg = cfg.model_copy(update={"scoring": scoring})
    if cfg.auth.people_file is not None:
        people_file = (base / cfg.auth.people_file).resolve()
        cfg = cfg.model_copy(
            update={"auth": cfg.auth.model_copy(update={"people_file": people_file})}
        )
    if isinstance(cfg.storage, StorageConfig):
        storage = cfg.storage.model_copy(update={"path": (base / cfg.storage.path).resolve()})
        cfg = cfg.model_copy(update={"storage": storage})
    issues = validate_config(cfg)
    errors = [i.message for i in issues if i.level == "error"]
    if errors:
        raise ConfigError(f"{path}: {errors[0]}{_more(len(errors) - 1)}")
    return cfg, issues


# Operational sections: tuning the deployment never reads as a config change.
_OPERATIONAL_SECTIONS = {"database", "storage", "worker", "api", "auth"}
# credentials for reaching the scorer: they do not shape output, and leaving them out keeps the
# hash of a config without them what it was before they existed
_SCORER_CREDENTIALS = {"api_key_env", "extra_headers_env"}


def config_hash(cfg: CoglossaConfig) -> str:
    """Identifies the settings that shape output; operational sections are excluded."""
    exclude: dict[str, Any] = dict.fromkeys(_OPERATIONAL_SECTIONS, True)
    exclude["scorer"] = _SCORER_CREDENTIALS
    # `stream` counts only when turned off, so a config that leaves it at its default keeps the
    # hash it had before the setting existed
    exclude["backends"] = {name: {"stream"} for name, b in cfg.backends.items() if b.stream}
    dump = cfg.model_dump_json(exclude=exclude)
    digest = hashlib.sha256(dump.encode("utf-8"))
    # The tiers file's path is in the dump; its content shapes the scores too. With no file set
    # nothing is added, so such a config keeps the hash it had before. A file that cannot be read
    # adds nothing either: loading it fails with its own message.
    tiers_file = cfg.scoring.language_tiers_file
    if tiers_file is not None:
        try:
            tiers_content = tiers_file.read_bytes()
        except OSError:
            tiers_content = None
        if tiers_content is not None:
            digest.update(b"\0language_tiers_file\0" + tiers_content)
    return digest.hexdigest()[:16]
