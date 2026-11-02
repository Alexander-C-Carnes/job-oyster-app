"""The outgoing-request guard (jobpipe/egress.py, docs/paid-service.md item 1.2): in hosted mode only public addresses
are reached, checked after DNS and on every redirect, and the connection goes to the checked address."""
import asyncio
import re
from pathlib import Path

import httpx
import pytest

from jobpipe import egress, hiringcafe, hosted, jd
from jobpipe.llm import OpenAICompatRunner

PUBLIC, PUBLIC6 = "93.184.215.14", "2606:4700:4700::1111"
NOT_PUBLIC = ["127.0.0.1", "127.8.8.8", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.100.100.200",
              "0.0.0.0", "192.0.2.1", "224.0.0.1", "240.0.0.1", "255.255.255.255", "::1", "::", "fe80::1",
              "fd00:ec2::254", "::ffff:127.0.0.1", "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe",
              "2002:7f00:1::", "2001:0:4136:e378:8000:63bf:3fff:fdd2", "ff02::1"]


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(hosted.SWITCH, "1")


class Net:
    """A fake DNS and network: names resolve from `dns` (a list of answers is used up one lookup at a time), and every
    request that gets through is recorded and answered by `answer`."""
    def __init__(self, dns, answer=None):
        self.dns, self.sent = dns, []
        self.answer = answer or (lambda r: httpx.Response(200, text="ok"))

    def resolve(self, host, port):
        a = self.dns[host]
        return a.pop(0) if a and isinstance(a[0], list) else a

    def handler(self, request):
        self.sent.append(request)
        return self.answer(request)

    def client(self, **kw):
        return egress.client(transport=httpx.MockTransport(self.handler), resolve=self.resolve, **kw)


@pytest.mark.parametrize("address", NOT_PUBLIC)
def test_non_public_addresses(address):
    assert not egress.is_public(address)


@pytest.mark.parametrize("address", [PUBLIC, "1.1.1.1", PUBLIC6, "::ffff:1.1.1.1"])
def test_public_addresses(address):
    assert egress.is_public(address)


@pytest.mark.parametrize("address", NOT_PUBLIC)
def test_hosted_refuses_a_name_or_literal_that_isnt_public(on, address):
    net = Net({"inside.example": [address]})
    lit = f"[{address}]" if ":" in address else address
    for url in ("http://inside.example/", f"http://{lit}/", f"https://{lit}:8443/latest/meta-data/"):
        with pytest.raises(egress.Blocked):
            net.client().get(url)
    assert net.sent == []


def test_hosted_refuses_a_name_with_any_non_public_address(on):
    net = Net({"split.example": [PUBLIC, "10.0.0.5"]})
    with pytest.raises(egress.Blocked, match="10.0.0.5"):
        net.client().get("https://split.example/")
    assert net.sent == []


def test_only_http_and_https(on):
    for url in ("file:///etc/passwd", "ftp://example.com/", "gopher://example.com/"):
        with pytest.raises((egress.Blocked, httpx.UnsupportedProtocol)):
            egress.client().get(url)


def test_the_connection_goes_to_the_checked_address_with_the_hosts_name(on):
    net = Net({"jobs.example": [PUBLIC6]})
    r = net.client().get("https://jobs.example/p/1")
    sent = net.sent[0]
    assert sent.url.host == PUBLIC6 and sent.headers["host"] == "jobs.example"
    assert sent.extensions["sni_hostname"] == "jobs.example"
    assert r.request.url.host == "jobs.example"                 # the client still sees the name


def test_dns_rebinding_cant_reach_inside(on):
    net = Net({"rebind.example": [[PUBLIC], ["127.0.0.1"]]})     # public for the first lookup, loopback after
    c = net.client()
    c.get("http://rebind.example/")
    assert [r.url.host for r in net.sent] == [PUBLIC]            # connected to what was checked, not the name
    with pytest.raises(egress.Blocked):
        c.get("http://rebind.example/")
    assert len(net.sent) == 1


@pytest.mark.parametrize("to", ["http://127.0.0.1:8765/api/summary", "http://inside.example/", "http://[::1]/",
                                "http://169.254.169.254/latest/meta-data/"])
def test_every_redirect_hop_is_checked(on, to):
    net = Net({"jobs.example": [PUBLIC], "inside.example": ["192.168.0.10"]},
              lambda r: httpx.Response(302, headers={"location": to}) if r.headers["host"] == "jobs.example"
              else httpx.Response(200, text="secret"))
    with pytest.raises(egress.Blocked):
        net.client(follow_redirects=True).get("https://jobs.example/apply")
    assert len(net.sent) == 1


def test_a_relative_redirect_stays_on_the_host(on):
    net = Net({"jobs.example": [PUBLIC]},
              lambda r: httpx.Response(301, headers={"location": "/new"}) if r.url.path == "/old" else httpx.Response(200))
    r = net.client(follow_redirects=True).get("https://jobs.example/old")
    assert r.status_code == 200 and str(r.url) == "https://jobs.example/new"
    assert [(s.url.host, s.headers["host"], s.url.path) for s in net.sent] == \
        [(PUBLIC, "jobs.example", "/old"), (PUBLIC, "jobs.example", "/new")]


def test_the_async_client_is_guarded_too(on):
    net = Net({"ai.example": [PUBLIC], "local.example": ["127.0.0.1"]})

    async def go():
        async with egress.async_client(transport=httpx.MockTransport(net.handler), resolve=net.resolve) as c:
            await c.get("https://ai.example/v1/models")
            with pytest.raises(egress.Blocked):
                await c.get("http://local.example:11434/v1/models")
    asyncio.run(go())
    assert [r.url.host for r in net.sent] == [PUBLIC]


def test_with_hosted_mode_off_requests_go_as_before(monkeypatch):
    monkeypatch.setenv(hosted.SWITCH, "0")
    net = Net({})
    assert net.client().get("http://127.0.0.1:11434/v1/models").status_code == 200   # a local AI on this Mac
    assert net.sent[0].url.host == "127.0.0.1"
    assert not isinstance(egress.client()._transport, egress.GuardTransport)


@pytest.fixture
def no_network(monkeypatch):
    def refuse(self, request):
        raise AssertionError(f"reached the network: {request.url}")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


def test_the_apps_own_requests_are_guarded(on, no_network):
    assert jd.read_posting("http://169.254.169.254/latest/meta-data/")["description"] == ""
    r = OpenAICompatRunner(provider="ollama", base_url="http://127.0.0.1:11434/v1", retry_wait=0)
    with pytest.raises(Exception, match="public address"):
        asyncio.run(r.models())


class FakeCurl:
    """curl_cffi's requests module, answering from `pages` (url -> (status, location))."""
    def __init__(self, pages):
        self.pages, self.pins, self.gets = pages, [], []
        outer = self

        class Session:
            def __init__(self, impersonate, timeout, curl_options):
                outer.pins.append(curl_options[CurlOpt.RESOLVE])

            def get(self, url, allow_redirects):
                assert allow_redirects is False
                outer.gets.append(url)
                status, location = outer.pages[url]
                return type("R", (), {"status_code": status, "text": "page", "headers": {"location": location}})()

            def close(self):
                pass
        self.Session = Session


class CurlOpt:
    RESOLVE = "resolve"


def test_hiringcafe_pages_are_guarded_hop_by_hop(on, monkeypatch):
    dns = {"hiringcafe.com": [PUBLIC], "evil.example": ["10.1.2.3"]}
    monkeypatch.setattr(egress, "system_resolve", lambda h, p: dns[h])
    monkeypatch.setattr(egress.public_address, "__defaults__", (egress.system_resolve,))
    curl = FakeCurl({"https://hiringcafe.com/s/a": (302, "/classic?x=1"), "https://hiringcafe.com/classic?x=1": (200, ""),
                     "https://hiringcafe.com/s/b": (302, "http://evil.example/")})
    fetch = hiringcafe._guarded_fetch(5, curl, CurlOpt, OSError)
    assert fetch("https://hiringcafe.com/s/a").status == 200
    assert curl.pins == [[f"hiringcafe.com:443:{PUBLIC}"]] * 2
    assert fetch("https://hiringcafe.com/s/a", False).location == "/classic?x=1"
    with pytest.raises(hiringcafe.Unreadable, match="public address"):
        fetch("https://hiringcafe.com/s/b")
    assert "http://evil.example/" not in curl.gets


# Every way the app's code could send a request, and the only places allowed to use one directly.
DIRECT = re.compile(r"httpx\.(Client|AsyncClient|get|post|put|patch|delete|head|request|stream)\(|"
                    r"httpx\.(Async)?HTTPTransport\(|requests\.(Session|get|post|request)\(|urlopen\(|"
                    r"(Async)?Anthropic\(|socket\.(create_connection|socket)\(|aiohttp|http\.client|pycurl")
ALLOWED = {
    "jobpipe/egress.py": 6,          # the guard itself
    "jobpipe/evidence_remote.py": 1,  # the honesty-check service, whose address the server's operator sets
    "jobpipe/doh.py": 1,             # DoHTransport's inner transport; jobs_api wraps DoHTransport in egress.client
    "jobpipe/hiringcafe.py": 2,      # curl: the unguarded session runs only with hosted mode off
    "jobpipe/llm.py": 1,             # the Claude API at its own address; in hosted mode make_runner gives the proxy's
    "jobpipe/pipeline.py": 4,        # the AI proxy, whose address the server's operator sets (hosted.PROXY_URL)
}


def test_no_request_bypasses_the_guard():
    root = Path(__file__).resolve().parents[1]
    found: dict[str, int] = {}
    for f in sorted((root / "jobpipe").rglob("*.py")):
        n = len(DIRECT.findall(f.read_text()))
        if n:
            found[f.relative_to(root).as_posix()] = n
    assert found == ALLOWED, "send requests through egress.client() / egress.async_client() (jobpipe/egress.py)"
