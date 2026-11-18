"""CI check: the Modal app imports (no Modal account needed) and has the functions the hosted
config and README rely on, with the endpoint labels app.py defines as constants. It also imports
app.py the way a Modal container does (as /root/app.py, with modal.is_local() False), which has
no repository around it. Run: python check_app.py

Modal has no public way to list an app's functions before deploy; App.registered_functions is
deprecated (removed in modal 1.7.0) but is the only one, so a bump past 1.6 needs this updated."""

import sys
import types
import warnings
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import modal  # noqa: E402

import app  # noqa: E402

FUNCTIONS = ["api", "cron", "doctor", "kick", "migrate", "scorer", "worker"]
EXPECTED_LABELS = {"API_LABEL": "coglossa-api", "KICK_LABEL": "coglossa-kick", "SCORER_LABEL": "coglossa-scorer"}


with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    functions = sorted(app.app.registered_functions)

problems = []
if functions != FUNCTIONS:
    problems.append(f"functions {functions}, expected {FUNCTIONS}")
labels = {"API_LABEL": app.API_LABEL, "KICK_LABEL": app.KICK_LABEL, "SCORER_LABEL": app.SCORER_LABEL}
if labels != EXPECTED_LABELS:
    problems.append(f"labels {labels}, expected {EXPECTED_LABELS}")


def import_as_in_a_container() -> types.ModuleType:
    """Run settings.py and app.py from /root, as Modal mounts them, with modal.is_local()
    False; returns the app module."""
    modules = {}
    with mock.patch.object(modal, "is_local", lambda: False), mock.patch.dict(sys.modules):
        for name in ("settings", "app"):
            module = types.ModuleType(name)
            module.__file__ = f"/root/{name}.py"
            sys.modules[name] = module
            source = (HERE / f"{name}.py").read_text(encoding="utf-8")
            exec(compile(source, module.__file__, "exec"), module.__dict__)
            modules[name] = module
    return modules["app"]


try:
    remote = import_as_in_a_container()
    if remote.REPO != Path("/"):
        problems.append(f"in a container REPO is {remote.REPO}, expected /")
except Exception as e:  # report every problem at once, not only the first
    problems.append(f"app.py fails to import in a container: {type(e).__name__}: {e}")
if problems:
    sys.exit("check_app: " + "; ".join(problems))
print(functions)
