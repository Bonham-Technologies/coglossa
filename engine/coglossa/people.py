"""Teams and admins from the people file (`auth.people_file`, CGL-26): the fallback for a
sign-in proxy that cannot pass group names. Read at startup; a change takes effect on restart."""

import re
import string
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

TEAM_NAME_PATTERN = r"^[A-Za-z0-9._-](?:[A-Za-z0-9._ -]{0,62}[A-Za-z0-9._-])?$"
_TEAM_NAME = re.compile(TEAM_NAME_PATTERN)
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
# The whitespace trimmed from the ends of a name: ASCII only (CGL-31). Other spaces (U+00A0,
# U+3000, ...) are part of the name, so "root" followed by an ideographic space is not "root".
NAME_SPACE = " \t\n\r\x0b\x0c"


def strip_name(name: str) -> str:
    return name.strip(NAME_SPACE)


def team_key(name: str) -> str:
    """People's names and team names compare in Unicode NFC, ignoring the case of A-Z only
    (CGL-31). Other letters must match as written: that is what the database's lower() does
    under C collation (access.visible folds the same way, as SQL), and it never joins two
    different names the way full case folding can ("ß" and "ss"). NFC itself joins only
    canonically equivalent spellings ("e" + U+0301 and "é"; the Kelvin sign and "K")."""
    return strip_name(unicodedata.normalize("NFC", name)).translate(_ASCII_LOWER)


def nfc(text: str) -> str:
    """Names are stored and compared in NFC, so "é" typed one way matches "é" typed another."""
    return unicodedata.normalize("NFC", text)


@dataclass(frozen=True)
class TeamFilter:
    """CGL-74: which names may be teams (`auth.team_groups_prefix`, `auth.team_groups`). With
    neither set, every name may; otherwise a name may if it starts with the prefix or is on
    the list. Names compare as team_key says (NFC, ASCII ends trimmed, A-Z in any case). It
    applies to every team, the proxy's groups and the people file's teams alike, so a set's
    team, the team picker and who sees a set all agree."""

    prefix: str | None = None  # NFC, A-Z lowered, not trimmed (a prefix may end in a space)
    names: frozenset[str] | None = None  # team_key of each listed name
    admin: str | None = None  # team_key of auth.admin_group, never allowed while a filter is on

    @classmethod
    def of(
        cls, prefix: str | None, names: Iterable[str] | None, admin_group: str | None = None
    ) -> "TeamFilter":
        """`admin_group` is left out whatever the prefix or list say, but only while a filter
        is on: with neither set, every name is allowed, exactly as before CGL-74."""
        active = prefix is not None or names is not None
        return cls(
            prefix=None if prefix is None else nfc(prefix).translate(_ASCII_LOWER),
            names=None if names is None else frozenset(team_key(n) for n in names),
            admin=team_key(admin_group) if active and admin_group is not None else None,
        )

    @property
    def active(self) -> bool:
        return self.prefix is not None or self.names is not None

    def allows(self, team: str) -> bool:
        if not self.active:
            return True
        key = team_key(team)
        if key == self.admin:
            return False
        return (self.names is not None and key in self.names) or (
            self.prefix is not None and key.startswith(self.prefix)
        )


ALL_TEAMS = TeamFilter()  # no filter: every group is a team (as before CGL-74)


class PeopleFileError(Exception):
    """The people file is missing or invalid; the message is one line."""


class _PeopleFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admins: list[str] = []
    teams: dict[str, list[str]] = {}

    @field_validator("teams")
    @classmethod
    def _team_names(cls, teams: dict[str, list[str]]) -> dict[str, list[str]]:
        for name in teams:
            if not _TEAM_NAME.fullmatch(name):
                raise ValueError(
                    f"team name '{name}': use 1-64 letters, digits, spaces, '.', '_' or '-'"
                )
        return teams


@dataclass(frozen=True)
class People:
    admins: frozenset[str] = frozenset()  # team_key of each admin's name
    teams_by_person: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def teams_of(self, name: str) -> tuple[str, ...]:
        return self.teams_by_person.get(team_key(name), ())

    def is_admin(self, name: str) -> bool:
        return team_key(name) in self.admins

    def all_teams(self) -> list[str]:
        names = {team_key(t): t for teams in self.teams_by_person.values() for t in teams}
        return sorted(names.values(), key=team_key)


NOBODY = People()


def load_people(path: Path | None) -> People:
    if path is None:
        return NOBODY
    if not path.is_file():
        raise PeopleFileError(f"people file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        data = _PeopleFile.model_validate({} if raw is None else raw)
    except yaml.YAMLError as e:
        raise PeopleFileError(f"{path}: invalid YAML: {' '.join(str(e).split())}") from e
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "top level"
        message = " ".join(str(first["msg"]).split())
        raise PeopleFileError(f"{path}: {where}: {message}") from e
    except (OSError, UnicodeDecodeError) as e:
        raise PeopleFileError(f"cannot read people file {path}: {e}") from e
    by_person: dict[str, list[str]] = {}
    for team, members in data.teams.items():
        for member in members:
            by_person.setdefault(team_key(member), []).append(team)
    return People(
        admins=frozenset(team_key(a) for a in data.admins),
        teams_by_person={k: tuple(v) for k, v in by_person.items()},
    )
