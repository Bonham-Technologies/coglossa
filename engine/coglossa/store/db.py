"""Database URLs and the async engine (SQLAlchemy 2 + psycopg 3)."""

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from coglossa.config import CoglossaConfig, ConfigError

_EXAMPLE = "postgresql://user:password@host:5432/coglossa"
# Without a connect timeout a firewalled host hangs every command for the OS TCP timeout.
CONNECT_TIMEOUT_S = 10
CONNECT_ARGS = {"connect_timeout": CONNECT_TIMEOUT_S}  # psycopg/libpq connection parameter


def _parse(url: str) -> URL:
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError):  # ValueError: a non-numeric port
        # never echo the URL or the parser's message: either can contain the password
        raise ConfigError(f"invalid database URL; expected {_EXAMPLE}") from None
    if parsed.drivername.split("+")[0] not in ("postgresql", "postgres"):
        raise ConfigError(f"unsupported database '{parsed.drivername}'; expected {_EXAMPLE}")
    return parsed


def to_sqlalchemy_url(url: str) -> str:
    return _parse(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def to_libpq_url(url: str) -> str:
    return _parse(url).set(drivername="postgresql").render_as_string(hide_password=False)


# libpq also takes a password in the query string (`?password=...`); mask those too.
_SECRET_QUERY_KEYS = ("password", "sslpassword")


def redact(url: str) -> str:
    parsed = _parse(url)
    secrets = [k for k in _SECRET_QUERY_KEYS if k in parsed.query]
    if secrets:
        parsed = parsed.update_query_dict(dict.fromkeys(secrets, "***"))
    rendered = parsed.render_as_string(hide_password=True)
    return rendered.replace("=%2A%2A%2A", "=***")


def error_reason(e: BaseException) -> str:
    """One line for a user: the driver's message (SQLAlchemy wraps it in `orig`)."""
    original = getattr(e, "orig", None) or e
    lines = str(original).strip().splitlines()
    return lines[0] if lines else type(original).__name__


def database_url(cfg: CoglossaConfig) -> str:
    url = cfg.database.url()
    if url is None:
        raise ConfigError(f"no database configured: set {cfg.database.url_env} to {_EXAMPLE}")
    _parse(url)
    return url


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine: AsyncEngine = create_async_engine(
            to_sqlalchemy_url(url), pool_pre_ping=True, connect_args=dict(CONNECT_ARGS)
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        return self.sessions()

    async def ping(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()
