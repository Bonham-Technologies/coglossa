"""`coglossa doctor` results (spec §7.4), shown by the CLI and the System page (M4)."""

from enum import StrEnum

from pydantic import BaseModel


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class DoctorCheck(BaseModel):
    name: str
    status: CheckStatus
    message: str
    fix: str = ""


class DoctorReport(BaseModel):
    checks: list[DoctorCheck]
    ok: bool

    @classmethod
    def of(cls, checks: list[DoctorCheck]) -> "DoctorReport":
        return cls(checks=checks, ok=all(c.status is not CheckStatus.FAIL for c in checks))
