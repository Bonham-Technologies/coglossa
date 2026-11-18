"""Export JSON Schema for the canonical models. Usage: python -m coglossa.schema.export DIR"""

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from coglossa.schema.doctor import DoctorReport
from coglossa.schema.guide import Guide
from coglossa.schema.report import Report
from coglossa.schema.run import RunProgress
from coglossa.schema.scorer_api import (
    QERequest,
    QEResponse,
    ScorerHealth,
    SimilarityRequest,
    SimilarityResponse,
)

EXPORTED: dict[str, type[BaseModel]] = {
    "report": Report,
    "guide": Guide,
    "run-progress": RunProgress,
    "scorer-qe-request": QERequest,
    "scorer-qe-response": QEResponse,
    "scorer-similarity-request": SimilarityRequest,
    "scorer-similarity-response": SimilarityResponse,
    "scorer-health": ScorerHealth,
    "doctor-report": DoctorReport,
}


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in EXPORTED.items():
        path = out_dir / f"{name}.schema.json"
        text = json.dumps(model.model_json_schema(), indent=2, sort_keys=True, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out_dir = Path(args[0]) if args else Path("../schema")
    for path in export(out_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
