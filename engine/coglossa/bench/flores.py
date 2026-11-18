"""FLORES-200 (NLLB Team et al., CC BY-SA 4.0): 1,012 devtest and 997 dev sentences in 204
language varieties, aligned line by line. Bench reads it from a local directory; only
`fetch_flores` downloads, on a machine with internet access (spec §9.2, §10)."""

import difflib
import hashlib
import io
import posixpath
import shutil
import tarfile
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx

from coglossa.bench.codes import FLORES_CODES
from coglossa.config import ConfigError

FLORES_URL = "https://dl.fbaipublicfiles.com/nllb/flores200_dataset.tar.gz"
FLORES_SHA256 = "b8b0b76783024b85797e5cc75064eb83fc5288b41e9654dabc7be6ae944011f6"
SPLITS = ("dev", "devtest")
_DATASET = "flores200_dataset"
_REGION_SCRIPT = {"tw": "hant", "hk": "hant", "mo": "hant", "cn": "hans", "sg": "hans"}
LICENSE_TEXT = """FLORES-200 is licensed under the Creative Commons Attribution-ShareAlike 4.0
International licence (CC BY-SA 4.0): https://creativecommons.org/licenses/by-sa/4.0/
"""
_CITATION = """FLORES-200 evaluation benchmark. NLLB Team, Marta R. Costa-jussà et al.,
"No Language Left Behind: Scaling Human-Centered Machine Translation", 2022.
https://github.com/facebookresearch/flores — CC BY-SA 4.0."""
ATTRIBUTION_TEXT = _CITATION + " Unmodified.\n"
_CHANGES = "Changes to the FLORES-200 text in this folder (CC BY-SA 4.0 asks that they be shown):"
FLAGS_MODIFIED = (
    "flags/*.jsonl contains FLORES-200 reference translations with deliberately injected errors "
    "(omissions, changed numbers and names, replaced locked terms, negations, register shifts), "
    "for testing."
)
# CLDR likely scripts for the languages FLORES has in two scripts, used for a bare tag.
_LIKELY_SCRIPT = {
    "zh": "hans",
    "ks": "arab",
    "kr": "latn",
    "ace": "latn",
    "bjn": "latn",
    "min": "latn",
    "taq": "latn",
}
_BY_TAG = {tag.lower(): code for code, tag in FLORES_CODES.items()}
_BY_TAG_EXACT = {tag: code for code, tag in FLORES_CODES.items()}


def flores_code(tag: str, available: Iterable[str]) -> str:
    """The FLORES file code for a BCP-47 tag: language+script, then the bare language, which
    is accepted only when its file is in the asked-for script (sr-Latn never reads srp_Cyrl).
    A region implies a script for Chinese (zh-TW -> zh-Hant); a bare tag of a language FLORES
    has in two scripts takes CLDR's likely script (zh -> zh-Hans, ks -> ks-Arab)."""
    codes = set(available)
    parts = tag.replace("_", "-").lower().split("-")
    lang = parts[0]
    script = next((p for p in parts[1:] if len(p) == 4 and p.isalpha()), None)
    region = next((p for p in parts[1:] if len(p) == 2), None)
    if script is None and lang == "zh":
        script = _REGION_SCRIPT.get(region)
    if script is None:
        script = _LIKELY_SCRIPT.get(lang)
    for candidate in [f"{lang}-{script}", lang] if script else [lang]:
        code = _BY_TAG.get(candidate)
        if code in codes and (script is None or code.rsplit("_", 1)[1].lower() == script):
            return code
    here = sorted(FLORES_CODES[c] for c in codes if c in FLORES_CODES)
    raise ConfigError(
        f"no FLORES-200 data for language '{tag}' (in this copy); did you mean: "
        f"{', '.join(_suggest(tag, lang, here))}? Languages here: {', '.join(here)}"
    )


def _suggest(tag: str, lang: str, here: list[str]) -> list[str]:
    """Up to three tags from `here` for an unknown tag: the same language in other scripts,
    then tags whose language (or ISO 639-3 code: `sp` -> `spa` -> `es`) starts like it, then
    the closest spellings."""
    code_of = {_BY_TAG_EXACT[t]: t for t in here}  # FLORES code -> tag
    same = [t for t in here if t.lower().split("-")[0] == lang]
    alike = [
        t for c, t in sorted(code_of.items()) if t.lower().startswith(lang) or c.startswith(lang)
    ]
    lower = {t.lower(): t for t in here}
    close = [lower[t] for t in difflib.get_close_matches(tag.lower(), lower, n=3, cutoff=0.0)]
    return list(dict.fromkeys(same + alike + close))[:3]


class FloresData:
    """A local FLORES-200 copy: `<root>/flores200_dataset/<split>/<code>.<split>`."""

    splits = SPLITS

    def __init__(self, root: Path) -> None:
        self.root = root
        self._dataset = root / _DATASET
        if not all((self._dataset / split).is_dir() for split in SPLITS):
            raise ConfigError(
                f"no FLORES-200 data at {root}: expected {_DATASET}/dev and devtest "
                "(coglossa bench fetch-flores)"
            )

    def codes(self) -> set[str]:
        """The FLORES codes this copy has (the files of `dev`)."""
        return {p.stem for p in (self._dataset / "dev").iterdir() if p.suffix == ".dev"}

    def sentences(self, tag: str, split: str) -> list[str]:
        """The sentences of one language and split, in FLORES order."""
        path = self._path(tag, split)
        lines = path.read_text(encoding="utf-8").split("\n")
        if lines[-1] == "":  # the newline that ends the last sentence
            lines.pop()
        for n, line in enumerate(lines, 1):
            if not line.strip():
                raise ConfigError(f"{path}: line {n} is blank; the data is damaged")
        return lines

    def pair(self, src: str, tgt: str, split: str) -> list[tuple[str, str]]:
        """Aligned (source, reference) sentences for one language pair."""
        a, b = self.sentences(src, split), self.sentences(tgt, split)
        if len(a) != len(b):
            raise ConfigError(
                f"FLORES-200 files {self._path(src, split)} and {self._path(tgt, split)} "
                f"have {len(a)} and {len(b)} lines; the data is damaged"
            )
        return list(zip(a, b, strict=True))

    def _path(self, tag: str, split: str) -> Path:
        if split not in SPLITS:
            raise ConfigError(f"split must be dev or devtest, not '{split}'")
        return self._dataset / split / f"{flores_code(tag, self.codes())}.{split}"


def _download(url: str) -> bytes:
    try:
        response = httpx.get(url, follow_redirects=True, timeout=300)
        response.raise_for_status()
    except httpx.HTTPError as e:
        detail = str(e).splitlines()[0] if str(e) else type(e).__name__  # httpx adds a help line
        raise ConfigError(f"could not download FLORES-200 from {url}: {detail}") from e
    return response.content


def write_attribution(out: Path, *, modified: str | None = None) -> None:
    """Put FLORES-200's licence and attribution beside bench output that holds its text. A
    `modified` note says what was changed; every note written to `out` is kept, so a later
    plain write never hides one."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / "ATTRIBUTION.txt"
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    listed = old.partition(f"{_CHANGES}\n")[2].splitlines()
    notes = [line.removeprefix("- ") for line in listed if line.startswith("- ")]
    if modified and modified not in notes:
        notes.append(modified)
    text = ATTRIBUTION_TEXT
    if notes:
        text = f"{_CITATION}\n{_CHANGES}\n" + "".join(f"- {note}\n" for note in notes)
    (out / "LICENSE.txt").write_text(LICENSE_TEXT, encoding="utf-8")
    path.write_text(text, encoding="utf-8")


def fetch_flores(
    dest: Path,
    *,
    download: Callable[[str], bytes] | None = None,
    log: Callable[[str], None] = print,
) -> Path:
    """Download FLORES-200 once, check it against the pinned SHA-256, and extract it into
    `dest` with its licence and attribution beside it. Returns `dest`."""
    log(f"downloading FLORES-200 from {FLORES_URL}")
    blob = (download or _download)(FLORES_URL)
    if hashlib.sha256(blob).hexdigest() != FLORES_SHA256:
        raise ConfigError("the FLORES-200 download does not match its checksum; try again")
    dest.mkdir(parents=True, exist_ok=True)
    # Attribution first, so even an interrupted extraction never leaves data without it.
    (dest / "LICENSE.txt").write_text(LICENSE_TEXT, encoding="utf-8")
    (dest / "ATTRIBUTION.txt").write_text(ATTRIBUTION_TEXT, encoding="utf-8")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            # The archive's names start "./flores200_dataset/"; normalising drops the "./".
            name = posixpath.normpath(member.name)
            inside = name.startswith(f"{_DATASET}/") and ".." not in name.split("/")
            if not (member.isfile() and inside):
                continue  # only regular files, and only inside the dataset directory
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    log(f"FLORES-200 is in {dest / _DATASET}")
    return dest
