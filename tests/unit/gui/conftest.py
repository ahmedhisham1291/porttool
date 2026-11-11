"""Fixtures of the GUI tests: a fake service, a running server and a small HTTP client."""

from __future__ import annotations

import http.client
import io
import json
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import openpyxl
import pytest

from porttool.core.models import Model
from porttool.gui import TOKEN_HEADER, GuiSettings, ServerHandle, serve
from tests.helpers.sample_model import build_sample_model
from tests.unit.gui.fake_service import FakeService, build_large_model

_UNSET = "<default>"


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]  # names in lower case
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    @property
    def error_code(self) -> str:
        return str(self.json()["error"]["code"])


class Client:
    """Talks to one server with ``http.client`` so tests control every header."""

    def __init__(self, handle: ServerHandle) -> None:
        self.handle = handle

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        token: str | None = _UNSET,
        host: str | None = _UNSET,
    ) -> Response:
        connection = http.client.HTTPConnection("127.0.0.1", self.handle.port, timeout=20)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            if host == _UNSET:
                host = f"127.0.0.1:{self.handle.port}"
            if host is not None:
                connection.putheader("Host", host)
            if token == _UNSET:
                token = self.handle.token
            if token is not None:
                connection.putheader(TOKEN_HEADER, token)
            sent = dict(headers or {})
            if body is not None and "Content-Length" not in sent:
                sent["Content-Length"] = str(len(body))
            for name, value in sent.items():
                connection.putheader(name, value)
            connection.endheaders(body)
            reply = connection.getresponse()
            data = reply.read()
            return Response(reply.status, {k.lower(): v for k, v in reply.getheaders()}, data)
        finally:
            connection.close()

    def get(self, path: str, **kwargs: Any) -> Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, payload: object | None = None, **kwargs: Any) -> Response:
        if payload is not None:
            kwargs["body"] = json.dumps(payload).encode("utf-8")
            kwargs.setdefault("headers", {"Content-Type": "application/json"})
        kwargs.setdefault("body", b"")
        return self.request("POST", path, **kwargs)

    def ok(self, path: str) -> Any:
        """GET that must succeed; returns the parsed JSON."""
        reply = self.get(path)
        assert reply.status == 200, reply.body
        return reply.json()

    def start(self, path: str, payload: object | None = None) -> str:
        """POST that must start a job; returns the job id."""
        reply = self.post(path, payload)
        assert reply.status == 200, reply.body
        return str(reply.json()["job_id"])

    def wait_job(self, job_id: str, timeout: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            job: dict[str, Any] = self.ok(f"/api/jobs/{job_id}")
            if not job["active"]:
                return job
            assert time.monotonic() < deadline, f"job {job_id} did not finish: {job}"
            time.sleep(0.02)

    def wait_tables(self, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            listed: dict[str, Any] = self.ok("/api/tables")
            if listed["status"] != "preparing":
                return listed
            assert time.monotonic() < deadline, "the tables were not prepared in time"
            time.sleep(0.02)


def multipart(
    files: list[tuple[str, bytes]], boundary: str = "----pt-test-boundary"
) -> tuple[str, bytes]:
    """Content type and body of a multipart/form-data upload."""
    parts: list[bytes] = []
    for name, data in files:
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        )
        parts.append(head.encode("utf-8") + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(parts)


def xlsx_bytes(cell: str = "request") -> bytes:
    """A real, small .xlsx file made with openpyxl."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Ports"
    sheet["A1"] = cell
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.fixture(scope="session")
def sample_model() -> Model:
    return build_sample_model()


@pytest.fixture(scope="session")
def large_model() -> Model:
    return build_large_model()


Starter = Callable[..., tuple[FakeService, Client]]


@pytest.fixture
def start_server() -> Iterator[Starter]:
    """Factory: ``service, client = start_server(model=..., loaded=..., settings=...)``."""
    handles: list[ServerHandle] = []
    services: list[FakeService] = []

    def start(
        service: FakeService | None = None,
        *,
        settings: GuiSettings | None = None,
        **service_args: Any,
    ) -> tuple[FakeService, Client]:
        fake = service if service is not None else FakeService(**service_args)
        handle = serve(fake, open_browser=False, settings=settings)
        handles.append(handle)
        services.append(fake)
        return fake, Client(handle)

    yield start
    for handle in handles:
        handle.close()
    for fake in services:
        fake.close()


@pytest.fixture
def service(start_server: Starter) -> FakeService:
    return start_server()[0]


@pytest.fixture
def client(start_server: Starter) -> Client:
    """A server with the sample model loaded and its tables prepared."""
    _, started = start_server()
    started.wait_tables()
    return started
