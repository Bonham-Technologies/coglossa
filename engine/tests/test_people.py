import pytest

from coglossa.people import NOBODY, PeopleFileError, load_people, team_key


def _write(tmp_path, text):
    path = tmp_path / "people.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_people_file_gives_teams_and_admins(tmp_path):
    people = load_people(
        _write(
            tmp_path,
            "admins: [Ana]\nteams:\n  spanish-desk: [maria, Jose]\n  Pacific: [tevita, ana]\n",
        )
    )
    assert people.teams_of("MARIA") == ("spanish-desk",)
    assert people.teams_of("ana") == ("Pacific",)
    assert people.teams_of("nobody") == ()
    assert people.is_admin("ana") and not people.is_admin("maria")
    assert people.all_teams() == ["Pacific", "spanish-desk"]


def test_an_empty_file_is_nobody_in_particular(tmp_path):
    people = load_people(_write(tmp_path, ""))
    assert people.all_teams() == [] and not people.is_admin("ana")


def test_no_file_means_nobody():
    assert load_people(None) is NOBODY
    assert NOBODY.teams_of("x") == () and not NOBODY.is_admin("x") and NOBODY.all_teams() == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("teams: [a]\n", "teams"),
        ("admins: [ana]\nother: 1\n", "other"),
        ("teams:\n  'bad/name': [ana]\n", "bad/name"),
        ("teams:\n  ok: ana\n", "ok"),
        ("- a\n- b\n", "top level"),
        ("teams: [\n", "invalid YAML"),
    ],
)
def test_a_bad_people_file_is_one_line(tmp_path, text, message):
    with pytest.raises(PeopleFileError, match=message) as err:
        load_people(_write(tmp_path, text))
    assert "\n" not in str(err.value) and "people.yaml" in str(err.value)


def test_a_missing_people_file(tmp_path):
    with pytest.raises(PeopleFileError, match="not found"):
        load_people(tmp_path / "absent.yaml")


def test_an_unreadable_people_file(tmp_path):
    path = tmp_path / "people.yaml"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(PeopleFileError, match="cannot read people file"):
        load_people(path)


def test_team_key():
    assert team_key(" Spanish Desk ") == "spanish desk"


def test_team_key_folds_only_a_to_z_and_normalises_accents():
    """CGL-31: what the database's translate() does, so lists and links agree; NFC, so a name
    typed with a combining accent matches the same name typed with one letter."""
    assert team_key(" Jose\u0301 ") == "jos\u00e9"
    assert team_key("Émilie") == "Émilie" != team_key("émilie")
    assert team_key("\u00c9MILIE") == team_key("\u00c9milie")
    assert team_key("Stra\u00dfe") != team_key("strasse")
    assert team_key("\troot \n") == "root" != team_key("root\u3000") != team_key("\u00a0root")


def test_people_file_names_match_in_nfc(tmp_path):
    people = load_people(_write(tmp_path, "admins: [Jose\u0301]\nteams:\n  desk: [Ren\u00e9e]\n"))
    assert people.is_admin("José") and people.teams_of("Rene\u0301e") == ("desk",)
