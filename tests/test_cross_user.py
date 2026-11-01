"""Two users, two copies of the app (docs/paid-service.md item 1.5): every route of one copy, called with every argument
pointing at the other's data, must not read it, change it or write into it.

Both copies run as the website runs them: `python -m jobpipe serve` in hosted mode, each with its own profile folder,
token and port. They're kept offline (a sitecustomize refuses every address but loopback), so nothing here reaches
the network, and the AI proxy's address refuses connections.
"""
import hashlib
import os
import re
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from jobpipe import config
from test_web import env  # noqa: F401 - the app, for its route table

OFFLINE = '''import socket
_LOCAL = ("127.0.0.1", "localhost", "::1")
_getaddrinfo, _connect, _connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex
def _host(address):
    return address[0] if isinstance(address, tuple) else None
def getaddrinfo(host, *a, **k):
    if host not in _LOCAL:
        raise socket.gaierror(f"offline: {host}")
    return _getaddrinfo(host, *a, **k)
def connect(self, address):
    if _host(address) not in (None, *_LOCAL):
        raise OSError(f"offline: {address}")
    return _connect(self, address)
def connect_ex(self, address):
    if _host(address) not in (None, *_LOCAL):
        return 111
    return _connect_ex(self, address)
socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = getaddrinfo, connect, connect_ex
'''
DESCRIPTION = ("Staff Technical Program Manager. Lead programs across platform, infrastructure and product teams, "
               "own planning, dependencies and launch readiness, and report risks to leadership every week. ") * 4


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Copy:
    """One user's copy of the app: its profile folder, port, token, and a secret only it holds."""
    def __init__(self, root: Path, name: str, offline: Path):
        self.home, self.port, self.secret = root / name, free_port(), f"SECRET-{name}-{os.urandom(6).hex()}"
        self.home.mkdir()
        keep = {k: v for k, v in os.environ.items() if not k.startswith(("JOBPIPE_", "OYSTER_"))}
        self.log = open(root / f"{name}.log", "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "jobpipe", "serve", "--port", str(self.port), "--no-open"], cwd=config.ROOT,
            stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True,
            env={**keep, "JOBPIPE_HOME": str(self.home), "OYSTER_HOSTED": "1", "OYSTER_AI_PROXY_URL": "http://127.0.0.1:9/v1",
                 "OYSTER_AI_PROXY_TOKEN": self.secret, "JOBPIPE_EVIDENCE_MODELS": "off", "NOTION_TOKEN": "",
                 "JOBSPIPE_API_KEY": "", "CLAUDE_BIN": "/usr/bin/false",
                 "PYTHONPATH": os.pathsep.join([str(offline), str(config.ROOT)])})
        self.url = f"http://127.0.0.1:{self.port}"
        for _ in range(300):
            try:
                if httpx.get(self.url + "/", timeout=1).status_code == 200 and (self.home / "data" / "web-token").exists():
                    break
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"{name} didn't start: {(root / f'{name}.log').read_text()[-2000:]}")
            time.sleep(0.2)
        self.token = (self.home / "data" / "web-token").read_text().strip()
        self.http = httpx.Client(base_url=self.url, headers={"X-Jobpipe-Token": self.token}, timeout=30)
        # Data only this user has: a job, the impact record and a résumé carrying the secret.
        r = self.http.post("/api/jobs", json={"job_title": "Staff TPM", "company": f"{name} Corp",
                                              "description": f"{DESCRIPTION} {self.secret}"})
        assert r.status_code == 200, r.text
        self.job = r.json()["id"]
        refs = self.home / "references"
        (refs / "impact-record.md").write_text((refs / "impact-record.md").read_text() + f"\n\n{self.secret}\n")
        (refs / "resume-secret.md").write_text(f"# Résumé\n\n{self.secret}\n")

    def stop(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)       # the server and any run it started
        except ProcessLookupError:
            pass
        self.log.close()


def files(home: Path) -> dict[str, str]:
    return {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(home.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def two(tmp_path_factory):
    root = tmp_path_factory.mktemp("cross-user")
    offline = root / "offline"
    offline.mkdir()
    (offline / "sitecustomize.py").write_text(OFFLINE)
    a = b = None
    try:
        a = Copy(root, "a", offline)
        b = Copy(root, "b", offline)
        yield a, b
    finally:
        for c in (a, b):
            if c:
                c.stop()


def body_keys() -> list[str]:
    """Every key the server reads from a request body."""
    src = "\n".join(p.read_text() for p in (config.ROOT / "jobpipe" / "web").glob("*.py"))
    return sorted(set(re.findall(r"""body(?:\.get\(|\[)["'](\w+)["']""", src)))


def pointing_at(a: "Copy", b: "Copy") -> list[str]:
    """Arguments that name the other user's data, every way a route might take them."""
    record = b.home / "references" / "impact-record.md"
    return [str(b.home), str(record), str(b.home / "data"), str(b.home / "data" / "tracker.db"), f"file://{record}",
            os.path.relpath(record, a.home / "data"), os.path.relpath(record, a.home / "references"),
            "../" * 12 + str(record).lstrip("/"), b.job, f"{b.url}/api/jobs?token={b.token}"]


def test_one_user_cant_reach_anothers_data(two, env):  # noqa: F811
    a, b = two
    before = files(b.home)
    keys = body_keys()
    values = pointing_at(a, b)
    calls = []
    for route in env["app"].routes:
        params = list(getattr(route, "param_convertors", {}))
        query = [q.alias for q in route.dependant.query_params] if hasattr(route, "dependant") else []
        for method in sorted((getattr(route, "methods", None) or {"GET"}) - {"HEAD"}):
            for v in values:
                for jid in ({v, a.job} if params else {None}):
                    path = route.path
                    for p in params:
                        path = path.replace("{" + p + "}", quote(jid if p in ("jid", "job_id") else v, safe="")) \
                                   .replace("{" + p + ":path}", quote(v, safe=""))
                    q = {k: v for k in query}
                    calls.append((method, path, q, v))
    assert len(calls) > 1000            # every route, every argument

    def call(c):
        method, path, q, v = c
        out = []
        try:
            if method in ("GET", "DELETE"):
                out.append(a.http.request(method, path, params=q))
            else:
                out.append(a.http.request(method, path, params=q, json={**{k: v for k in keys},
                                                                        "job_ids": [v, b.job], "ids": [v, b.job]}))
                out.append(a.http.request(method, path, params=q, data={k: v for k in keys},
                                          files={"file": (v, f"upload {a.secret}".encode())}))
        except httpx.TransportError:      # a slow AI call or a dropped connection: nothing came back to read
            pass
        return [(method, path, v, r.status_code, b.secret.encode() in r.content) for r in out]

    with ThreadPoolExecutor(8) as pool:
        answers = [x for found in pool.map(call, calls) for x in found]
    leaks = [x[:4] for x in answers if x[4]]
    assert leaks == [], f"{a.home.name} read {b.home.name}'s data: {leaks[:5]}"
    assert sum(x[3] < 300 for x in answers) > 300          # the routes did run, not only turn the requests away

    assert a.proc.poll() is None and b.proc.poll() is None
    after = files(b.home)
    for name in ("references/impact-record.md", "references/resume-secret.md"):
        assert after.get(name) == before[name], f"{name} was changed"
    assert set(before) <= set(after), f"files gone: {set(before) - set(after)}"
    wrote = [n for n in after if a.secret.encode() in (b.home / n).read_bytes()]
    assert wrote == [], f"{a.home.name} wrote into {b.home.name}'s folder: {wrote}"
    # The secret would be seen: each copy shows its own to its own user.
    assert a.secret in a.http.get(f"/api/jobs/{a.job}").text and b.secret in b.http.get(f"/api/jobs/{b.job}").text
    # and each copy answers only to its own token
    assert httpx.get(f"{b.url}/api/summary", headers={"X-Jobpipe-Token": a.token}).status_code == 401
    assert b.http.get("/api/jobs").status_code == 200
