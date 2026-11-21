"""A real browser for tests of the static-search components, with no new package.

Runs an installed Chromium (Edge, Chrome or Chromium; CLIO_TEST_BROWSER names
another) headless, and talks to it over the DevTools protocol with
``websockets``, which uvicorn[standard] already brings. Every host but
127.0.0.1 is unreachable, as on a network that blocks the CDNs, and the
static server records each path asked for, so a test can say what a page
did and did not download.

    with static_server(folder) as server, chromium() as browser:
        page = browser.page()
        page.goto(server.url("index.html"))
        page.wait_for("document.querySelector('clio-search').search")

Tests that need it use the ``browser`` fixture in tests/conftest.py, which
skips them when no such browser is installed.
"""

from __future__ import annotations

import contextlib
import http.server
import itertools
import json
import os
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
import urllib.request
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

CANDIDATES = [
    os.environ.get("CLIO_TEST_BROWSER") or "",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge",
]


def find_browser() -> Optional[str]:
    for candidate in CANDIDATES:
        if not candidate:
            continue
        if os.path.isabs(candidate) and os.path.isfile(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


class _Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".js": "text/javascript", ".mjs": "text/javascript", ".wasm": "application/wasm",
                      ".json": "application/json", ".bin": "application/octet-stream",
                      ".webmanifest": "application/manifest+json", ".vtt": "text/vtt", ".xml": "application/xml",
                      ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8"}

    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        self.server.requests.append(self.path)
        super().do_GET()


class StaticServer:
    def __init__(self, folder: Path):
        handler = partial(_Handler, directory=str(folder))
        self.httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.httpd.requests = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def requests(self) -> List[str]:
        return self.httpd.requests

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/{path.lstrip('/')}"

    def asked_for(self, fragment: str) -> List[str]:
        return [r for r in self.requests if fragment in r]


@contextlib.contextmanager
def static_server(folder: Path):
    server = StaticServer(folder)
    server.thread.start()
    try:
        yield server
    finally:
        server.httpd.shutdown()
        server.httpd.server_close()


class Page:
    """One tab, driven over the DevTools protocol."""

    def __init__(self, ws_url: str):
        from websockets.sync.client import connect

        self._connection = connect(ws_url, max_size=None, open_timeout=20)
        self.ws = self._connection.__enter__()
        self.ids = itertools.count(1)
        self.events: List[Dict[str, Any]] = []
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("Network.enable")

    def send(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 30) -> Dict[str, Any]:
        msg_id = next(self.ids)
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            message = json.loads(self.ws.recv(timeout=max(0.1, deadline - time.time())))
            if message.get("id") == msg_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self.events.append(message)

    def goto(self, url: str, timeout: float = 30) -> None:
        self.events.clear()
        self.send("Page.navigate", {"url": url})
        self.wait_for("document.readyState === 'complete'", timeout=timeout)

    def eval(self, expression: str, timeout: float = 30) -> Any:
        result = self.send("Runtime.evaluate", {"expression": expression, "awaitPromise": True,
                                                "returnByValue": True}, timeout=timeout)
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            raise AssertionError(details.get("exception", {}).get("description") or details.get("text"))
        return result.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout: float = 30) -> Any:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = self.eval(f"(() => {{ try {{ return {expression} }} catch (e) {{ return null }} }})()")
            except Exception:  # noqa: BLE001 - the page may be navigating
                last = None
            if last:
                return last
            time.sleep(0.1)
        raise AssertionError(f"Timed out waiting for {expression}; console: {self.console()[-5:]}")

    def type_into(self, element: str, text: str) -> None:
        """Set an input's value and fire "input", as typing would. ``element`` is a JS expression."""
        self.eval(f"(() => {{ const i = {element}; i.focus(); i.value = {json.dumps(text)};"
                  " i.dispatchEvent(new Event('input', { bubbles: true })) })()")

    def console(self) -> List[str]:
        out = []
        for e in self.events:
            if e.get("method") == "Runtime.consoleAPICalled":
                out.append(" ".join(str(a.get("value", a.get("description", ""))) for a in e["params"]["args"]))
            elif e.get("method") == "Runtime.exceptionThrown":
                d = e["params"]["exceptionDetails"]
                out.append("exception: " + (d.get("exception", {}).get("description") or d.get("text", "")))
        return out

    def drain(self) -> None:
        """Read the events waiting on the socket (requests made since the last call)."""
        try:
            while True:
                self.events.append(json.loads(self.ws.recv(timeout=0.05)))
        except TimeoutError:
            pass

    def requested(self) -> List[str]:
        """Every URL the page asked for, from any origin, since the last goto."""
        self.drain()
        return [e["params"]["request"]["url"] for e in self.events if e.get("method") == "Network.requestWillBeSent"]

    def dialogs(self) -> List[str]:
        self.drain()
        return [e["params"]["message"] for e in self.events if e.get("method") == "Page.javascriptDialogOpening"]

    def set_offline(self, offline: bool) -> None:
        self.send("Network.emulateNetworkConditions", {"offline": offline, "latency": 0,
                                                       "downloadThroughput": -1, "uploadThroughput": -1})

    def throttle(self, *, latency_ms: float, down_bytes_per_s: float) -> None:
        self.send("Network.emulateNetworkConditions", {"offline": False, "latency": latency_ms,
                                                       "downloadThroughput": down_bytes_per_s,
                                                       "uploadThroughput": down_bytes_per_s})

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.ws.close()


class Browser:
    def __init__(self, executable: str):
        self.profile = tempfile.mkdtemp(prefix="clio-browser-")
        self.process = subprocess.Popen(
            [executable, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
             "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
             "--disable-background-networking", "--disable-component-update", "--mute-audio",
             "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = Path(self.profile) / "DevToolsActivePort"
        deadline = time.time() + 30
        # The launcher may hand over to a browser process of its own and exit
        # (Edge on Windows does), so wait for the port file, not the process.
        while not port_file.exists() or not port_file.read_text().strip():
            if time.time() > deadline:
                self.close()
                raise RuntimeError("The browser did not start.")
            time.sleep(0.1)
        self.port = int(port_file.read_text().split()[0])
        self.pages: List[Page] = []

    def page(self) -> Page:
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(request, timeout=10) as r:
            target = json.loads(r.read())
        page = Page(target["webSocketDebuggerUrl"])
        self.pages.append(page)
        return page

    def close(self) -> None:
        for page in getattr(self, "pages", []):
            page.close()
        if getattr(self, "port", None):
            with contextlib.suppress(Exception):
                from websockets.sync.client import connect

                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=5) as r:
                    ws_url = json.loads(r.read())["webSocketDebuggerUrl"]
                with connect(ws_url, open_timeout=5) as ws:
                    ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
                    with contextlib.suppress(Exception):
                        ws.recv(timeout=5)
            time.sleep(0.5)
        with contextlib.suppress(Exception):
            self.process.terminate()
            self.process.wait(timeout=10)
        with contextlib.suppress(Exception):
            self.process.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


@contextlib.contextmanager
def chromium():
    executable = find_browser()
    if not executable:
        raise RuntimeError("No Chromium-based browser is installed.")
    browser = Browser(executable)
    try:
        yield browser
    finally:
        browser.close()
