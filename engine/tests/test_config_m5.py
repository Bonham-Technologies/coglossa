from pathlib import Path

from coglossa.cli import build_parser
from coglossa.config import CONFIG_ENV, default_config_path


def test_the_config_path_defaults_to_the_environment(monkeypatch):
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    assert default_config_path() == Path("coglossa.yaml")
    monkeypatch.setenv(CONFIG_ENV, "/etc/coglossa/coglossa.yaml")
    assert default_config_path() == Path("/etc/coglossa/coglossa.yaml")
    monkeypatch.setenv(CONFIG_ENV, "")
    assert default_config_path() == Path("coglossa.yaml")


def test_every_command_takes_the_environments_config(monkeypatch):
    monkeypatch.setenv(CONFIG_ENV, "/etc/coglossa/coglossa.yaml")
    parser = build_parser()
    for argv in (
        ["doctor"],
        ["worker"],
        ["db", "upgrade"],
        ["status", "r"],
        ["run", "x.txt", "--target-lang", "en"],
    ):
        assert parser.parse_args(argv).config == Path("/etc/coglossa/coglossa.yaml"), argv
