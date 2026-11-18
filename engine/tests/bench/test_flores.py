import hashlib
import io
import tarfile

import httpx
import pytest

from coglossa.bench import flores
from coglossa.bench.codes import FLORES_CODES
from coglossa.bench.flores import (
    ATTRIBUTION_TEXT,
    LICENSE_TEXT,
    FloresData,
    fetch_flores,
    flores_code,
    write_attribution,
)
from coglossa.config import ConfigError


def test_the_code_table_is_complete_and_invertible():
    assert len(FLORES_CODES) >= 200
    assert FLORES_CODES["eng_Latn"] == "en"
    assert FLORES_CODES["spa_Latn"] == "es"
    assert FLORES_CODES["zho_Hans"] == "zh-Hans"
    assert FLORES_CODES["zho_Hant"] == "zh-Hant"
    assert FLORES_CODES["yor_Latn"] == "yo"
    assert FLORES_CODES["arb_Arab"] == "ar"
    assert len(set(FLORES_CODES.values())) == len(FLORES_CODES)  # one tag per file


def test_reads_aligned_sentences(mini_flores):
    data = FloresData(mini_flores)
    assert data.codes() == {"eng_Latn", "spa_Latn", "yor_Latn", "zho_Hant", "por_Latn"}
    pairs = data.pair("en", "es", "devtest")
    assert len(pairs) == 8
    assert pairs[2] == (
        "It is not raining in Lagos today.",
        "[spa_Latn] It is not raining in Lagos today.",
    )


def test_tags_with_script_or_region(mini_flores):  # Review Focus 3
    codes = FloresData(mini_flores).codes()
    assert flores_code("pt-BR", codes) == "por_Latn"
    assert flores_code("zh-Hant", codes) == "zho_Hant"
    assert flores_code("zh-TW", codes) == "zho_Hant"
    assert flores_code("ZH-hant", codes) == "zho_Hant"
    assert flores_code("es", codes) == "spa_Latn"


def test_unknown_tag_suggests(mini_flores):  # Review Focus 3
    with pytest.raises(ConfigError) as e:
        flores_code("sp", FloresData(mini_flores).codes())
    message = str(e.value)
    assert message.startswith("no FLORES-200 data for language 'sp' (in this copy); did you mean: ")
    assert "es" in message.split("did you mean: ")[1].split("?")[0]
    assert message.endswith("? Languages here: en, es, pt, yo, zh-Hant")
    assert "\n" not in message


def test_missing_or_misaligned_data_is_a_clear_error(mini_flores, tmp_path):
    with pytest.raises(
        ConfigError, match="no FLORES-200 data at .*: expected flores200_dataset/dev"
    ):
        FloresData(tmp_path / "nothing")
    data = FloresData(mini_flores)
    with pytest.raises(ConfigError, match="split must be dev or devtest"):
        data.sentences("en", "test")
    short = mini_flores / "flores200_dataset" / "devtest" / "spa_Latn.devtest"
    short.write_text("one line\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="have 8 and 1 lines; the data is damaged"):
        data.pair("en", "es", "devtest")


def _tarball(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            raw = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def test_fetch_flores_verifies_and_extracts(tmp_path, monkeypatch):
    blob = _tarball(
        {
            "flores200_dataset/dev/eng_Latn.dev": "a\n",
            "flores200_dataset/devtest/eng_Latn.devtest": "b\n",
            "flores200_dataset/README": "readme\n",
            "../evil": "x",  # never extracted outside dest
        }
    )
    monkeypatch.setattr(flores, "FLORES_SHA256", hashlib.sha256(blob).hexdigest())
    root = fetch_flores(tmp_path / "out", download=lambda url: blob, log=lambda _: None)
    assert (root / "flores200_dataset" / "dev" / "eng_Latn.dev").read_text() == "a\n"
    assert not (tmp_path / "evil").exists()
    assert "CC BY-SA 4.0" in (root / "LICENSE.txt").read_text()
    assert "FLORES-200" in (root / "ATTRIBUTION.txt").read_text()


def test_fetch_flores_refuses_a_bad_download(tmp_path):
    with pytest.raises(ConfigError, match="the FLORES-200 download does not match its checksum"):
        fetch_flores(tmp_path / "out", download=lambda url: b"not the archive", log=lambda _: None)
    assert not (tmp_path / "out" / "flores200_dataset").exists()


def test_tags_resolve_against_the_real_table():
    real = FLORES_CODES.keys()
    assert flores_code("zh", real) == "zho_Hans"
    assert flores_code("zh-CN", real) == "zho_Hans"
    assert flores_code("zh-HK", real) == "zho_Hant"
    assert flores_code("ar", real) == "arb_Arab"
    assert flores_code("ar-Latn", real) == "arb_Latn"
    assert flores_code("ks-Deva", real) == "kas_Deva"
    assert flores_code("fa", real) == "pes_Arab"
    assert flores_code("sw", real) == "swh_Latn"
    assert flores_code("es_419", real) == "spa_Latn"


def test_a_script_this_copy_lacks_is_not_swapped_for_another():
    with pytest.raises(ConfigError, match="'zh-TW' .* Languages here: zh-Hans$"):
        flores_code("zh-TW", {"zho_Hans"})


def test_a_last_line_without_a_newline_is_still_a_sentence(mini_flores):
    path = mini_flores / "flores200_dataset" / "dev" / "yor_Latn.dev"
    path.write_text("one\ntwo", encoding="utf-8")
    assert FloresData(mini_flores).sentences("yo", "dev") == ["one", "two"]


def test_fetch_flores_downloads_with_httpx_by_default(tmp_path, monkeypatch):
    blob = _tarball({"./flores200_dataset/dev/eng_Latn.dev": "a\n"})  # the real archive's "./"
    monkeypatch.setattr(flores, "FLORES_SHA256", hashlib.sha256(blob).hexdigest())
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, content=blob, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    lines = []
    root = fetch_flores(tmp_path / "out", log=lines.append)
    assert calls == [(flores.FLORES_URL, {"follow_redirects": True, "timeout": 300})]
    assert (root / "flores200_dataset" / "dev" / "eng_Latn.dev").read_text() == "a\n"
    assert lines and all("\n" not in line for line in lines)


def _not_found(url, **kwargs):
    return httpx.Response(404, request=httpx.Request("GET", url))


def _unreachable(url, **kwargs):
    raise httpx.ConnectError("")


@pytest.mark.parametrize("get", [_not_found, _unreachable], ids=["status", "connect"])
def test_fetch_flores_reports_a_failed_download_on_one_line(tmp_path, monkeypatch, get):
    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(ConfigError, match="could not download FLORES-200 from https://") as e:
        fetch_flores(tmp_path / "out", log=lambda _: None)
    assert "\n" not in str(e.value)


@pytest.mark.parametrize(
    "tag", ["sr-Latn", "uz-Cyrl", "pa-Arab", "mn-Mong", "yue-Hans", "ar-Hebr", "ks-Latn"]
)
def test_an_explicit_script_never_reads_another_scripts_file(tag):  # fix round 1, finding 1
    with pytest.raises(ConfigError, match=f"no FLORES-200 data for language '{tag}'"):
        flores_code(tag, FLORES_CODES.keys())


@pytest.mark.parametrize(
    ("tag", "code"),
    [
        ("ar-Arab", "arb_Arab"),
        ("pt-Latn", "por_Latn"),
        ("sr-Cyrl", "srp_Cyrl"),
        ("de-1996", "deu_Latn"),  # a variant, not a script
        ("ks", "kas_Arab"),  # CLDR likely scripts for the two-script languages
        ("kr", "knc_Latn"),
        ("ace", "ace_Latn"),
        ("bjn", "bjn_Latn"),
        ("min", "min_Latn"),
        ("taq", "taq_Latn"),
        ("ks-Deva", "kas_Deva"),
        ("taq-Tfng", "taq_Tfng"),
    ],
)
def test_script_and_likely_script_resolution(tag, code):
    assert flores_code(tag, FLORES_CODES.keys()) == code


def _suggestions(tag, available):
    with pytest.raises(ConfigError) as e:
        flores_code(tag, available)
    message = str(e.value)
    assert "\n" not in message
    return message.split("did you mean: ")[1].split("? Languages here")[0].split(", ")


def test_suggestions_on_the_real_table():  # fix round 1, finding 2
    real = FLORES_CODES.keys()
    assert _suggestions("sp", real)[0] == "es"
    assert "nb" in _suggestions("no", real)
    assert "azb" in _suggestions("az-Arab", real)  # South Azerbaijani is the Arabic-script one
    assert _suggestions("sr-Latn", real)[0] == "sr"
    assert len(_suggestions("xx", real)) == 3


def test_a_bare_tag_whose_likely_script_is_missing_suggests_the_other():
    assert _suggestions("ks", {"kas_Deva", "eng_Latn"})[0] == "ks-Deva"


def test_attribution_is_written_before_extraction(tmp_path, monkeypatch):  # fix round 1, item 3
    blob = b"passes the checksum but is not a tarball"
    monkeypatch.setattr(flores, "FLORES_SHA256", hashlib.sha256(blob).hexdigest())
    with pytest.raises(tarfile.TarError):
        fetch_flores(tmp_path / "out", download=lambda url: blob, log=lambda _: None)
    assert "CC BY-SA 4.0" in (tmp_path / "out" / "LICENSE.txt").read_text()
    assert "FLORES-200" in (tmp_path / "out" / "ATTRIBUTION.txt").read_text()


def test_bench_output_gets_the_plain_attribution(tmp_path):
    write_attribution(tmp_path / "out")
    assert (tmp_path / "out" / "LICENSE.txt").read_text(encoding="utf-8") == LICENSE_TEXT
    assert (tmp_path / "out" / "ATTRIBUTION.txt").read_text(encoding="utf-8") == ATTRIBUTION_TEXT
    assert "Unmodified" in ATTRIBUTION_TEXT


def test_modified_text_is_declared_and_the_note_is_kept(tmp_path):
    out = tmp_path / "out"
    write_attribution(out)
    write_attribution(out, modified="flags/*.jsonl has injected errors.")
    write_attribution(out, modified="flags/*.jsonl has injected errors.")  # once, not twice
    write_attribution(out)  # a later plain write keeps the note
    write_attribution(out, modified="x.csv is shortened.")
    text = (out / "ATTRIBUTION.txt").read_text(encoding="utf-8")
    assert "Unmodified" not in text and "NLLB Team" in text and "CC BY-SA 4.0" in text
    assert text.endswith("- flags/*.jsonl has injected errors.\n- x.csv is shortened.\n"), text
    assert text.count("injected errors") == 1
