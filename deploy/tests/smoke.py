"""The air-gap smoke test (spec §9.1). It runs inside the stack's network with the app image's
Python and uses only the standard library:
  python - BASE_URL run NAME     the web app answers; doctor passes; project NAME translates a
                                 document end to end; every export downloads
  python - BASE_URL check NAME   project NAME is there and its document translates again: the
                                 database and the stored files survived (upgrade, restore)"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

TEXT = "Il pleut sur la ville.\n\nMarie dort. Le chat dort aussi.\n"
EXPORTS = {"docx": b"PK", "bilingual-docx": b"PK", "html": b"<", "json": b"{"}


def fail(message: str) -> None:
    sys.exit(f"FAIL: {message}")


def call(method: str, url: str, body: bytes | None = None, headers: dict | None = None):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def api(base: str, method: str, path: str, payload=None, expect: int = 200):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    status, data = call(method, base + path, body, headers)
    if status != expect:
        fail(f"{method} {path} answered {status}: {data[:300]!r}")
    return json.loads(data) if data else None


def upload(base: str, project_id: str, filename: str, text: str) -> str:
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode() + text.encode() + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    status, data = call("POST", f"{base}/api/projects/{project_id}/runs", body, headers)
    if status != 202:
        fail(f"upload answered {status}: {data[:300]!r}")
    return json.loads(data)["id"]


def wait_until_complete(base: str, run_id: str, timeout_s: float = 300) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = api(base, "GET", f"/api/runs/{run_id}")["run"]["state"]
        if state == "complete":
            return
        if state in ("failed", "cancelled", "complete_with_errors", "awaiting_guide_approval"):
            fail(f"run {run_id} ended {state}")
        time.sleep(2)
    fail(f"run {run_id} did not finish within {timeout_s:.0f} s")


def check_exports(base: str, run_id: str) -> None:
    for kind, start in EXPORTS.items():
        status, data = call("GET", f"{base}/api/runs/{run_id}/exports/{kind}")
        if status != 200 or not data.lstrip().startswith(start):
            fail(f"export {kind} answered {status}, starting {data[:20]!r}")


def run(base: str, name: str) -> None:
    status, page = call("GET", base + "/")
    if status != 200 or b'<div id="root">' not in page:
        fail(f"the web app is not served at / ({status})")
    doctor = api(base, "GET", "/api/system/doctor")
    if not doctor["ok"]:
        fail(f"doctor: {[c for c in doctor['checks'] if c['status'] == 'fail']}")
    project = api(base, "POST", "/api/projects", {"name": name, "target_lang": "en"}, expect=201)
    run_id = upload(base, project["id"], "pluie.txt", TEXT)
    wait_until_complete(base, run_id)
    check_exports(base, run_id)
    print(f"ok: project {name} translated a document; every export downloads")


def check(base: str, name: str) -> None:
    projects = api(base, "GET", "/api/projects")
    project = next((p for p in projects if p["name"] == name), None)
    if project is None:
        fail(f"project {name} is gone")
    documents = api(base, "GET", f"/api/projects/{project['id']}/documents")
    if not documents:
        fail(f"project {name} has no documents")
    again = api(base, "POST", f"/api/documents/{documents[0]['id']}/runs", {}, expect=202)
    wait_until_complete(base, again["id"])
    check_exports(base, again["id"])
    print(f"ok: project {name} is there and its document translates again")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[2] not in ("run", "check"):
        sys.exit("usage: smoke.py BASE_URL run|check NAME")
    {"run": run, "check": check}[sys.argv[2]](sys.argv[1].rstrip("/"), sys.argv[3])
