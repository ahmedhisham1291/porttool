"""The HTTP server against the fake service: security checks and every endpoint."""

from __future__ import annotations

import http.client
import logging
import statistics
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from porttool.core.errors import ConfigError, GitError, RepoStateError
from porttool.core.models import Model, PortDirection
from porttool.core.operations import AddPort
from porttool.core.pending import PendingEntry, PendingQueue, PendingState
from porttool.core.sheets import COLUMNS, Sheet
from porttool.gui import TOKEN_HEADER, GuiSettings, serve
from porttool.gui.tables import TABLE_DEF_BY_NAME, TABLE_DEFS, build_tables
from tests.unit.gui.conftest import Client, Starter, multipart, xlsx_bytes
from tests.unit.gui.fake_service import FakeService

TABLE_NAMES = [
    "components",
    "ports",
    "runnable_access",
    "triggers",
    "connections",
    "unconnected",
    "interfaces",
    "interface_elements",
    "data_types",
    "pending",
]
PORT_ROW = {"sheet": "Ports", "values": {"swc": "SwcA", "port": "PpNew", "direction": "P"}}


# --------------------------------------------------------------------------- security


def test_api_call_without_token_is_refused(client: Client) -> None:
    reply = client.get("/api/state", token=None)
    assert reply.status == 403
    assert reply.error_code == "bad-token"


def test_api_call_with_wrong_token_is_refused(client: Client) -> None:
    for token in ("wrong", client.handle.token + "x", ""):
        reply = client.get("/api/state", token=token)
        assert (reply.status, reply.error_code) == (403, "bad-token")
    assert client.post("/api/load", token="wrong").status == 403


def test_token_in_the_query_string_is_not_accepted_by_the_api(client: Client) -> None:
    reply = client.get(f"/api/state?token={client.handle.token}", token=None)
    assert reply.status == 403


def test_page_is_served_without_token_and_never_cached(client: Client) -> None:
    reply = client.get("/", token=None)
    assert reply.status == 200
    assert reply.headers["content-type"].startswith("text/html")
    assert reply.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in reply.headers["content-security-policy"]
    assert client.handle.token.encode() not in reply.body
    assert client.get(f"/?token={client.handle.token}", token=None).status == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:80", "127.0.0.1", "[::1]", None])
def test_foreign_host_header_is_refused(client: Client, host: str | None) -> None:
    for path in ("/", "/api/state", "/static/app.js"):
        reply = client.get(path, host=host)
        assert (reply.status, reply.error_code) == (403, "bad-host"), path


def test_host_header_with_another_port_is_refused(client: Client) -> None:
    other = client.handle.port + 1
    assert client.get("/api/state", host=f"127.0.0.1:{other}").status == 403
    assert client.get("/api/state", host=f"evil.example:{client.handle.port}").status == 403


def test_localhost_host_header_is_accepted(client: Client) -> None:
    assert client.get("/api/state", host=f"localhost:{client.handle.port}").status == 200
    assert client.get("/api/state", host=f"LOCALHOST:{client.handle.port}").status == 200


def test_request_from_another_origin_is_refused(client: Client) -> None:
    reply = client.post("/api/load", headers={"Origin": "http://evil.example"})
    assert (reply.status, reply.error_code) == (403, "bad-origin")
    own = {"Origin": f"http://127.0.0.1:{client.handle.port}"}
    assert client.get("/api/state", headers=own).status == 200


def test_no_cors_headers_are_sent(client: Client) -> None:
    replies = [
        client.get("/"),
        client.get("/api/state"),
        client.get("/api/state", token=None),
        client.request("OPTIONS", "/api/state", headers={"Origin": "http://evil.example"}),
    ]
    for reply in replies:
        assert not [name for name in reply.headers if name.startswith("access-control")]
        assert reply.headers["cache-control"] == "no-store"
        assert reply.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    [
        "/static/../server.py",
        "/static/..%2fserver.py",
        "/static/%2e%2e/server.py",
        "/static/..\\server.py",
        "/static/%2e%2e%5cserver.py",
        "/static/sub/../../server.py",
        "/static//etc/passwd",
        "/static/.hidden",
        "/static/",
        "/../porttool/gui/server.py",
        "/server.py",
        "/static/app.js%00.html",
        "/static/missing.js",
    ],
)
def test_path_traversal_is_not_possible(client: Client, path: str) -> None:
    reply = client.get(path)
    assert reply.status == 404, path
    assert b"import " not in reply.body


def test_static_files_have_fixed_content_types(client: Client) -> None:
    for name, kind in (("app.js", "text/javascript"), ("app.css", "text/css")):
        reply = client.get(f"/static/{name}", token=None)
        assert reply.status == 200, name
        assert reply.headers["content-type"].startswith(kind)
        assert int(reply.headers["content-length"]) == len(reply.body) > 0


def test_unknown_addresses_and_wrong_methods(client: Client) -> None:
    assert client.get("/api/nothing").error_code == "not-found"
    assert client.get("/api/load").status == 405
    assert client.post("/api/state").status == 405
    assert client.request("DELETE", "/api/state").status == 405
    assert client.request("PUT", "/").status == 405
    assert client.get("/favicon.ico").status == 204


def test_post_without_content_length_is_refused(client: Client) -> None:
    reply = client.request("POST", "/api/load")
    assert (reply.status, reply.error_code) == (411, "length-required")


def test_invalid_json_body_is_a_400(client: Client) -> None:
    for body in (b"{not json", b"[1, 2]", b"\xff\xfe"):
        reply = client.post("/api/export", body=body)
        assert (reply.status, reply.error_code) == (400, "bad-json")


def test_server_binds_to_loopback_only(client: Client) -> None:
    assert client.handle.url.startswith(f"http://127.0.0.1:{client.handle.port}/?token=")
    assert client.handle.token not in client.handle.base_url


# --------------------------------------------------------------------------- state


def test_state_of_a_loaded_repo(client: Client, sample_model: Model) -> None:
    state = client.ok("/api/state")
    assert state["repo"] == str(Path("C:/ecu/repo"))
    assert state["config_present"] is True and state["model_loaded"] is True
    assert state["model"]["counts"]["ports"] == len(sample_model.ports) == 6
    assert state["model"]["counts"]["connectors"] == 2
    assert state["stale"] is False and state["recovery_needed"] is False
    assert state["jobs"] == []
    assert state["tables"] == "ready"
    assert state["validation"]["can_apply"] is False
    assert state["default_export_target"].endswith(".xlsx")
    assert "porttool_requests_" in state["default_export_target"]


def test_state_without_repo(start_server: Starter) -> None:
    _, client = start_server(loaded=False)
    state = client.ok("/api/state")
    assert state["repo"] is None and state["config_present"] is False
    assert state["model_loaded"] is False and state["model"] is None
    assert state["tables"] == "no-model"
    assert client.ok("/api/tables")["status"] == "no-model"
    assert client.ok("/api/tables/ports")["rows"] == []
    assert client.post("/api/load").error_code == "no-repo"
    assert client.post("/api/export", {"target": "/x.xlsx"}).error_code == "no-repo"


def test_state_shows_running_jobs(start_server: Starter) -> None:
    service, client = start_server()
    service.hang_next = True
    job_id = client.start("/api/load")
    jobs = client.ok("/api/state")["jobs"]
    assert [job["id"] for job in jobs] == [job_id]
    assert jobs[0]["name"] == "load" and jobs[0]["active"] is True
    assert "log_tail" not in jobs[0]
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["cancelled"] is True
    assert client.wait_job(job_id)["state"] == "cancelled"
    assert client.ok("/api/state")["jobs"] == []


def test_stale_and_recovery_flags_follow_the_service(start_server: Starter) -> None:
    service, client = start_server()
    assert client.ok("/api/state")["stale"] is False
    service.stale = True
    service.recovery = True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = client.ok("/api/state")
        if state["stale"] and state["recovery_needed"]:
            break
        time.sleep(0.02)
    assert state["stale"] is True and state["recovery_needed"] is True


# --------------------------------------------------------------------------- tables


def test_tables_list_has_every_table_with_columns(client: Client) -> None:
    listed = client.ok("/api/tables")
    assert listed["status"] == "ready"
    assert [table["name"] for table in listed["tables"]] == TABLE_NAMES
    by_name = {table["name"]: table for table in listed["tables"]}
    assert by_name["ports"]["rows"] == 6 and by_name["unconnected"]["rows"] == 2
    assert by_name["ports"]["only"] == ["unconnected"]
    headers = [column["header"] for column in by_name["ports"]["columns"]]
    assert headers[:4] == ["SWC", "Port", "Direction", "Interface"]
    assert all(column["width"] > 0 for column in by_name["ports"]["columns"])


def test_table_columns_reuse_the_sheet_columns() -> None:
    sheets = {
        "components": Sheet.COMPONENTS,
        "ports": Sheet.PORTS,
        "runnable_access": Sheet.RUNNABLE_ACCESS,
        "triggers": Sheet.RUNNABLE_TRIGGERS,
        "connections": Sheet.CONNECTIONS,
        "unconnected": Sheet.UNCONNECTED,
        "interfaces": Sheet.INTERFACES,
        "interface_elements": Sheet.INTERFACE_ELEMENTS,
        "data_types": Sheet.DATA_TYPES,
    }
    assert [table.name for table in TABLE_DEFS] == TABLE_NAMES
    for name, sheet in sheets.items():
        expected = {
            (c.key, c.header)
            for c in COLUMNS[sheet]
            if c.key not in ("action", "requester", "comment")
        }
        assert {(c.key, c.header) for c in TABLE_DEF_BY_NAME[name].columns} == expected


def test_sample_model_rows(client: Client) -> None:
    ports = client.ok("/api/tables/ports?sort=port")
    assert (ports["total"], ports["filtered"]) == (6, 6)
    assert [row[1] for row in ports["rows"]] == sorted(row[1] for row in ports["rows"])
    first = dict(
        zip([c.key for c in TABLE_DEF_BY_NAME["ports"].columns], ports["rows"][0], strict=True)
    )
    assert first["swc"] == "SwcB" and first["port"] == "PpDiag"
    assert first["interface_kind"] == "client-server" and first["connected"] == "Y"
    triggers = client.ok("/api/tables/triggers?q=periodic")["rows"]
    assert triggers[0][:7] == ["SwcA", "RunA_10ms", "periodic", "", "", "10", "Task_10ms"]
    connections = client.ok("/api/tables/connections?f.provider_swc=swca")["rows"]
    assert connections[0][:6] == ["SwcA", "PpSpeed", "SwcB", "RpSpeed", "EcuComposition"] + [
        "SwcA_PpSpeed_SwcB_RpSpeed"
    ]
    unconnected = client.ok("/api/tables/unconnected")["rows"]
    assert [row[:5] for row in unconnected] == [
        ["SwcA", "RpTorque", "R", "IfTorque", "SwcM.PpTorque"],
        ["SwcM", "PpTorque", "P", "IfTorque", "SwcA.RpTorque"],
    ]
    elements = client.ok("/api/tables/interface_elements?f.interface=ifdiag")["rows"]
    assert elements[0][:5] == ["IfDiag", "ReadDtc", "Dtc", "OUT", "uint16"]
    components = client.ok("/api/tables/components?f.arxml_owner=model")["rows"]
    assert [row[0] for row in components] == ["SwcM"]


def test_only_unconnected_shows_ports_without_connector(client: Client) -> None:
    page = client.ok("/api/tables/ports?only=unconnected")
    assert (page["total"], page["filtered"]) == (6, 2)
    assert sorted(row[1] for row in page["rows"]) == ["PpTorque", "RpTorque"]
    assert client.get("/api/tables/components?only=unconnected").error_code == "bad-query"
    assert client.get("/api/tables/ports?only=other").error_code == "bad-query"


@pytest.mark.parametrize(
    "query",
    [
        "offset=-1",
        "offset=abc",
        "limit=x",
        "sort=nothing",
        "dir=sideways",
        "f.nothing=a",
    ],
)
def test_bad_table_query_is_a_400(client: Client, query: str) -> None:
    reply = client.get(f"/api/tables/ports?{query}")
    assert (reply.status, reply.error_code) == (400, "bad-query")


def test_unknown_table_is_a_404(client: Client) -> None:
    reply = client.get("/api/tables/secrets")
    assert (reply.status, reply.error_code) == (404, "unknown-table")


def _is_number(text: str) -> bool:
    try:
        return float(text) == float(text)
    except ValueError:
        return False


def reference_query(
    rows: list[tuple[str, ...]],
    table: str,
    *,
    offset: int = 0,
    limit: int = 200,
    sort: str | None = None,
    desc: bool = False,
    q: str = "",
    filters: dict[str, str] | None = None,
    only: str | None = None,
) -> tuple[list[list[str]], int, int]:
    """A plain reimplementation of the table query: filter, then sort, then slice."""
    columns = TABLE_DEF_BY_NAME[table].columns
    keys = [column.key for column in columns]
    chosen = list(rows)
    if only == "unconnected":
        chosen = [row for row in chosen if row[keys.index("connected")] == "N"]
    if q.strip():
        needle = q.strip().lower()
        chosen = [row for row in chosen if any(needle in cell.lower() for cell in row)]
    for key, text in (filters or {}).items():
        if text.strip():
            index = keys.index(key)
            chosen = [row for row in chosen if text.strip().lower() in row[index].lower()]
    if sort is not None:
        index = keys.index(sort)
        if columns[index].numeric:
            chosen.sort(
                key=lambda row: (
                    (0, float(row[index]), "")
                    if _is_number(row[index])
                    else (1, 0.0, row[index].lower())
                ),
                reverse=desc,
            )
        else:
            chosen.sort(key=lambda row: row[index].lower(), reverse=desc)
    return [list(row) for row in chosen[offset : offset + limit]], len(rows), len(chosen)


QUERIES: list[tuple[str, str, dict[str, Any]]] = [
    ("ports", "", {}),
    ("ports", "offset=400&limit=37", {"offset": 400, "limit": 37}),
    ("ports", "offset=19990", {"offset": 19990}),
    ("ports", "offset=25000", {"offset": 25000}),
    ("ports", "limit=0", {"limit": 0}),
    ("ports", "sort=port&dir=asc", {"sort": "port"}),
    ("ports", "sort=port&dir=desc&offset=200", {"sort": "port", "desc": True, "offset": 200}),
    ("ports", "sort=partner_count&dir=desc", {"sort": "partner_count", "desc": True}),
    ("ports", "sort=interface&q=SWC_0001", {"sort": "interface", "q": "SWC_0001"}),
    ("ports", "q=%20rp_04%20", {"q": " rp_04 "}),
    ("ports", "q=no-such-text", {"q": "no-such-text"}),
    ("ports", "f.swc=swc_0003&f.direction=r", {"filters": {"swc": "swc_0003", "direction": "r"}}),
    (
        "ports",
        "f.interface=If_00&q=pp&sort=swc&dir=desc&offset=10&limit=500",
        {"filters": {"interface": "If_00"}, "q": "pp", "sort": "swc", "desc": True}
        | {"offset": 10, "limit": 500},
    ),
    ("ports", "only=unconnected&sort=port", {"only": "unconnected", "sort": "port"}),
    ("ports", "only=unconnected&f.swc=399", {"only": "unconnected", "filters": {"swc": "399"}}),
    ("ports", "f.port=", {"filters": {"port": ""}}),
    (
        "connections",
        "sort=connector_name&dir=desc&offset=14900",
        {"sort": "connector_name"} | {"desc": True, "offset": 14900},
    ),
    (
        "connections",
        "f.requester_port=rp_049&q=swc_001",
        {"filters": {"requester_port": "rp_049"}} | {"q": "swc_001"},
    ),
    (
        "unconnected",
        "sort=compatible_partners&limit=50",
        {"sort": "compatible_partners"} | {"limit": 50},
    ),
    (
        "components",
        "f.team=t3&sort=name&dir=desc",
        {"filters": {"team": "t3"}, "sort": "name"} | {"desc": True},
    ),
    ("triggers", "sort=period_ms", {"sort": "period_ms"}),
]


@pytest.fixture(scope="module")
def large_rows(large_model: Model) -> dict[str, list[tuple[str, ...]]]:
    return {name: data.rows for name, data in build_tables(large_model, None).items()}


@pytest.fixture
def large_client(start_server: Starter, large_model: Model) -> Client:
    _, client = start_server(model=large_model)
    client.wait_tables()
    return client


@pytest.mark.parametrize(
    ("table", "query", "expected"), QUERIES, ids=[q[1] or "-" for q in QUERIES]
)
def test_paging_sorting_filtering_match_the_reference(
    large_client: Client,
    large_rows: dict[str, list[tuple[str, ...]]],
    table: str,
    query: str,
    expected: dict[str, Any],
) -> None:
    page = large_client.ok(f"/api/tables/{table}?{query}")
    rows, total, filtered = reference_query(large_rows[table], table, **expected)
    assert page["status"] == "ready"
    assert (page["total"], page["filtered"]) == (total, filtered)
    assert page["rows"] == rows


def test_large_model_has_the_expected_size(large_client: Client) -> None:
    by_name = {t["name"]: t["rows"] for t in large_client.ok("/api/tables")["tables"]}
    assert by_name["ports"] == 20000 and by_name["connections"] == 15000


def test_limit_is_capped_at_500_rows(large_client: Client) -> None:
    page = large_client.ok("/api/tables/ports?limit=100000")
    assert page["limit"] == 500 and len(page["rows"]) == 500
    assert len(large_client.ok("/api/tables/ports")["rows"]) == 200


def test_filter_is_case_insensitive_substring(large_client: Client) -> None:
    lower = large_client.ok("/api/tables/ports?f.swc=swc_00012&q=PP_00")
    upper = large_client.ok("/api/tables/ports?f.swc=SWC_00012&q=pp_00")
    assert lower["rows"] == upper["rows"] and lower["filtered"] == 10


def test_tables_are_prepared_in_the_background(start_server: Starter, large_model: Model) -> None:
    _, client = start_server(model=large_model)
    started = time.perf_counter()
    first = client.ok("/api/tables/ports")
    first_ms = (time.perf_counter() - started) * 1000.0
    assert first["status"] in ("preparing", "ready")
    assert first_ms < 500, "the first call must not wait for the tables"
    assert client.wait_tables()["status"] == "ready"
    assert client.ok("/api/tables/ports")["total"] == 20000


WARM_PATHS = [
    "/api/state",
    "/api/tables",
    "/api/tables/ports",
    "/api/tables/ports?offset=19800",
    "/api/tables/ports?sort=port&dir=desc&offset=10000",
    "/api/tables/ports?q=swc_0012&sort=interface",
    "/api/tables/ports?f.interface=if_01&f.direction=r&sort=partner_count&dir=desc&limit=500",
    "/api/tables/connections?sort=connector_name&offset=7000&limit=500",
    "/api/tables/unconnected?q=rp",
    "/api/lists",
    "/api/lists?swc=Swc_00001",
    "/api/validation",
    "/api/jobs",
]


def test_no_handler_blocks_longer_than_50_ms_on_the_large_model(large_client: Client) -> None:
    """Measured per address after one warm-up call: the median of 9 calls must be < 50 ms.

    The first call of a new sort or filter is included in the 200 ms page budget.
    """
    report: list[str] = []
    for path in WARM_PATHS:
        started = time.perf_counter()
        large_client.ok(path)
        first_ms = (time.perf_counter() - started) * 1000.0
        assert first_ms < 200, f"{path}: first call took {first_ms:.0f} ms (budget 200 ms)"
        times: list[float] = []
        for _ in range(9):
            started = time.perf_counter()
            large_client.ok(path)
            times.append((time.perf_counter() - started) * 1000.0)
        median = statistics.median(times)
        report.append(
            f"{median:6.1f} ms median {max(times):6.1f} ms max {first_ms:6.1f} first {path}"
        )
        assert median < 50, f"{path}: median {median:.1f} ms"
    print("\n".join(report))


def test_concurrent_requests_get_correct_answers(
    large_client: Client, large_rows: dict[str, list[tuple[str, ...]]]
) -> None:
    errors: list[str] = []

    def worker(number: int) -> None:
        try:
            for step in range(6):
                offset = (number * 997 + step * 131) % 19000
                needle = f"swc_{number:04d}"
                page = large_client.ok(f"/api/tables/ports?offset={offset}&limit=50")
                rows, _, _ = reference_query(large_rows["ports"], "ports", offset=offset, limit=50)
                if page["rows"] != rows:
                    errors.append(f"worker {number}: wrong page at {offset}")
                found = large_client.ok(f"/api/tables/ports?q={needle}&sort=port&dir=desc")
                rows, _, count = reference_query(
                    large_rows["ports"], "ports", q=needle, sort="port", desc=True
                )
                if found["rows"] != rows or found["filtered"] != count:
                    errors.append(f"worker {number}: wrong search result")
                if large_client.ok("/api/state")["model"]["counts"]["ports"] != 20000:
                    errors.append(f"worker {number}: wrong state")
        except Exception as error:  # noqa: BLE001 - collected and reported below
            errors.append(f"worker {number}: {type(error).__name__}: {error}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert not errors, errors[:5]


# --------------------------------------------------------------------------- repo and jobs


def test_open_repo_then_load(start_server: Starter, tmp_path: Path) -> None:
    service, client = start_server(loaded=False)
    reply = client.post("/api/open-repo", {"repo": str(tmp_path)})
    assert reply.status == 200, reply.body
    data = reply.json()
    assert data["findings"][0]["code"] == "CFG-I001"
    assert data["state"]["repo"] == str(tmp_path) and data["state"]["model_loaded"] is False
    job = client.wait_job(client.start("/api/load"))
    assert job["state"] == "done" and job["summary"]["ports"] == 6
    assert job["log_tail"][-1] == "load finished"
    assert client.ok("/api/state")["model_loaded"] is True
    assert service.calls[:2] == ["open_repo", "start_load"]


def test_open_repo_problems_are_reported(start_server: Starter, tmp_path: Path) -> None:
    _, client = start_server(loaded=False)
    assert client.post("/api/open-repo", {"repo": "  "}).error_code == "bad-request"
    assert client.post("/api/open-repo", {"repo": 5}).error_code == "bad-request"
    reply = client.post("/api/open-repo", {"repo": str(tmp_path / "missing")})
    assert (reply.status, reply.error_code) == (400, "PT-E100")
    assert "does not exist" in reply.json()["error"]["message"]


def test_export_starts_a_job_for_the_target(start_server: Starter, tmp_path: Path) -> None:
    service, client = start_server()
    target = tmp_path / "requests.xlsx"
    reply = client.post("/api/export", {"target": str(target)})
    assert reply.status == 200 and reply.json()["target"] == str(target)
    job = client.wait_job(reply.json()["job_id"])
    assert job["name"] == "export" and job["summary"]["target"] == str(target)
    assert service.export_targets == [target]


def test_export_target_is_checked(client: Client, tmp_path: Path) -> None:
    bad = ["", "relative.xlsx", str(tmp_path / "x.xls"), str(tmp_path / "no" / "x.xlsx")]
    for target in bad:
        reply = client.post("/api/export", {"target": target})
        assert (reply.status, reply.error_code) == (400, "bad-request"), target
    assert client.ok("/api/jobs")["jobs"] == []


def test_only_one_job_runs_at_a_time(start_server: Starter, tmp_path: Path) -> None:
    service, client = start_server()
    service.hang_next = True
    job_id = client.start("/api/load")
    for path, payload in (
        ("/api/load", None),
        ("/api/doctor", None),
        ("/api/export", {"target": str(tmp_path / "x.xlsx")}),
        ("/api/dry-run/rows", {"rows": [PORT_ROW]}),
    ):
        reply = client.post(path, payload)
        assert (reply.status, reply.error_code) == (409, "job-running"), path
    assert len(client.ok("/api/jobs")["jobs"]) == 1
    client.post(f"/api/jobs/{job_id}/cancel")
    client.wait_job(job_id)
    assert client.wait_job(client.start("/api/doctor"))["summary"]["tools"] == "UNVERIFIED"


def test_job_endpoints(start_server: Starter) -> None:
    service, client = start_server(steps=4, step_s=0.05)
    job_id = client.start("/api/load")
    running = client.ok(f"/api/jobs/{job_id}")
    assert running["state"] in ("queued", "running") and running["total"] == 4
    done = client.wait_job(job_id)
    assert (done["state"], done["done"], done["error"]) == ("done", 4, None)
    assert done["elapsed_s"] >= 0.1
    time.sleep(0.15)
    assert client.ok(f"/api/jobs/{job_id}")["elapsed_s"] == done["elapsed_s"]
    listed = client.ok("/api/jobs")["jobs"]
    assert [(job["id"], job["state"]) for job in listed] == [(job_id, "done")]
    assert client.get("/api/jobs/nope").error_code == "unknown-job"
    assert client.post("/api/jobs/nope/cancel").status == 404
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["cancelled"] is False


def test_failed_job_reports_error_and_log_file(start_server: Starter) -> None:
    service, client = start_server()
    service.fail_next = "DVWspChecker reported 3 errors."
    job = client.wait_job(client.start("/api/load"))
    assert job["state"] == "failed" and job["error"] == "DVWspChecker reported 3 errors."
    assert "log_file" in job


# --------------------------------------------------------------------------- dry run, apply


def test_dry_run_rows_builds_raw_request_rows(start_server: Starter) -> None:
    service, client = start_server()
    rows = [
        {
            "sheet": "Ports",
            "values": {"swc": " SwcA ", "port": "PpNew", "direction": "P", "interface": "IfSpeed"},
            "requester": "Mona",
            "comment": "first",
        },
        {"sheet": "Connections", "values": {"provider_swc": "SwcA", "provider_port": "reject"}},
    ]
    reply = client.post("/api/dry-run/rows", {"rows": rows})
    assert reply.status == 200 and reply.json()["rows"] == 2
    client.wait_job(reply.json()["job_id"])
    first, second = service.dry_run_rows
    assert (first.sheet, first.action, first.requester, first.comment) == (
        "Ports",
        "ADD",
        "Mona",
        "first",
    )
    assert first.row_id is None and first.row.sheet == "Ports" and first.row.row == 1
    assert set(first.values) == {column.key for column in COLUMNS[Sheet.PORTS]}
    assert first.values["swc"] == "SwcA" and first.values["action"] == "ADD"
    assert first.values["init_value"] == "" and first.values["requester"] == "Mona"
    assert second.sheet == "Connections" and second.row.row == 2
    validation = client.ok("/api/validation")
    assert validation["counts"] == {"accepted": 1, "rejected": 1}
    assert validation["results"][1]["reasons"][0]["code"] == "VAL-NAME-001"
    assert validation["results"][0]["row"] == {"file": "Request form", "sheet": "Ports", "row": 1}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"rows": []},
        {"rows": "x"},
        {"rows": [5]},
        {"rows": [{"sheet": "Components", "values": {}}]},
        {"rows": [{"sheet": "Ports", "values": {"nothing": "x"}}]},
        {"rows": [{"sheet": "Ports", "values": {"port": ["a"]}}]},
        {"rows": [{"sheet": "Ports", "values": "x"}]},
        {"rows": [{"sheet": "Ports", "values": {}, "action": "EXPLODE"}]},
        {"rows": [{"sheet": "Ports", "values": {"port": "x" * 5000}}]},
    ],
)
def test_dry_run_rows_rejects_malformed_requests(client: Client, payload: object) -> None:
    reply = client.post("/api/dry-run/rows", payload)
    assert (reply.status, reply.error_code) == (400, "bad-request")
    assert client.ok("/api/jobs")["jobs"] == []


def test_upload_of_two_workbooks_reaches_the_service(start_server: Starter) -> None:
    service, client = start_server()
    first, second = xlsx_bytes("one"), xlsx_bytes("two")
    content_type, body = multipart([("Team A requests.xlsx", first), ("طلبات.xlsx", second)])
    reply = client.post("/api/dry-run/upload", body=body, headers={"Content-Type": content_type})
    assert reply.status == 200, reply.body
    assert [f["name"] for f in reply.json()["files"]] == ["Team A requests.xlsx", "طلبات.xlsx"]
    job = client.wait_job(reply.json()["job_id"])
    assert job["state"] == "done"
    assert [data for _, data in service.dry_run_files] == [first, second]
    names = [name for name, _ in service.dry_run_files]
    assert names == ["01_Team_A_requests.xlsx", "02_workbook.xlsx"]
    validation = client.ok("/api/validation")
    assert validation["total"] == 3
    assert validation["results"][0]["row"]["file"] == "Team A requests.xlsx"
    saved = list((client.handle.temp_dir / "uploads").rglob("*.xlsx"))
    assert len(saved) == 2 and all(client.handle.temp_dir in path.parents for path in saved)


def test_upload_of_a_raw_xlsx_body(start_server: Starter) -> None:
    service, client = start_server()
    data = xlsx_bytes()
    reply = client.post(
        "/api/dry-run/upload?name=..%2F..%2Fevil.xlsx",
        body=data,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert reply.status == 200, reply.body
    client.wait_job(reply.json()["job_id"])
    assert service.dry_run_files == [("01_evil.xlsx", data)]


def test_a_second_upload_replaces_the_saved_files(start_server: Starter) -> None:
    _, client = start_server()
    for _ in range(2):
        content_type, body = multipart([("a.xlsx", xlsx_bytes())])
        reply = client.post(
            "/api/dry-run/upload", body=body, headers={"Content-Type": content_type}
        )
        client.wait_job(reply.json()["job_id"])
    assert len(list((client.handle.temp_dir / "uploads").rglob("*.xlsx"))) == 1


def test_upload_limits(start_server: Starter) -> None:
    settings = GuiSettings(max_upload_mb=1, max_upload_files=2)
    service, client = start_server(settings=settings)
    content_type, body = multipart([("big.xlsx", xlsx_bytes() + b"\0" * (1024 * 1024))])
    reply = client.post("/api/dry-run/upload", body=body, headers={"Content-Type": content_type})
    assert (reply.status, reply.error_code) == (413, "too-large")
    assert "limit 1 MB" in reply.json()["error"]["message"]
    assert "connection" not in reply.headers  # the oversized body was read and dropped
    huge = {"Content-Type": content_type, "Content-Length": str(50 * 1024 * 1024 * 1024)}
    reply = client.post("/api/dry-run/upload", body=b"", headers=huge)
    assert (reply.status, reply.error_code) == (413, "too-large")
    assert reply.headers.get("connection") == "close"  # too big to read: connection ends
    reply = client.post("/api/export", body=b"", headers={"Content-Length": "-5"})
    assert (reply.status, reply.error_code) == (400, "bad-request")
    content_type, body = multipart([(f"{n}.xlsx", xlsx_bytes()) for n in range(3)])
    reply = client.post("/api/dry-run/upload", body=body, headers={"Content-Type": content_type})
    assert (reply.status, reply.error_code) == (400, "too-many-files")
    content_type, body = multipart([])
    reply = client.post("/api/dry-run/upload", body=body, headers={"Content-Type": content_type})
    assert (reply.status, reply.error_code) == (400, "bad-upload")
    reply = client.post(
        "/api/dry-run/upload", body=b"x", headers={"Content-Type": "multipart/form-data"}
    )
    assert (reply.status, reply.error_code) == (400, "bad-upload")
    assert "start_dry_run" not in service.calls
    assert not (client.handle.temp_dir / "uploads").exists()


def test_validation_view_and_status_filter(start_server: Starter) -> None:
    _, client = start_server()
    assert client.ok("/api/validation")["present"] is False
    rows = [
        PORT_ROW,
        {"sheet": "Ports", "values": {"port": "reject"}},
        {"sheet": "Ports", "values": {"swc": "SwcM", "port": "PpX"}, "comment": "model"},
        PORT_ROW,
    ]
    client.wait_job(client.start("/api/dry-run/rows", {"rows": rows}))
    validation = client.ok("/api/validation")
    assert validation["present"] is True and validation["total"] == 4
    assert validation["counts"] == {"accepted": 2, "rejected": 1, "waiting for model owner": 1}
    assert [r["status"] for r in validation["results"]] == [
        "accepted",
        "rejected",
        "waiting for model owner",
        "accepted",
    ]
    assert validation["plan"]["preview"] == [
        "add port PpNew to /ComponentTypes/SwcA",
        "add port PpNew to /ComponentTypes/SwcA",
    ]
    assert validation["plan"]["files"][0]["action"] == "modify"
    assert validation["findings"][0]["code"] == "VAL-INFO-001"
    assert validation["pending"] == 1
    assert validation["apply"] == {
        "present": True,
        "total": 4,
        "accepted": 2,
        "can_apply": True,
        "blockers": [],
    }
    rejected = client.ok("/api/validation?status=rejected")
    assert [r["request_id"] for r in rejected["results"]] == ["REQ-0002"]
    assert rejected["counts"] == validation["counts"]
    waiting = client.ok("/api/validation?status=waiting%20for%20model%20owner")
    assert len(waiting["results"]) == 1
    assert client.get("/api/validation?status=maybe").error_code == "bad-request"
    pending = client.ok("/api/tables/pending")
    assert pending["total"] == 1
    assert pending["rows"][0][:3] == ["waiting for model owner", "add_port"] + [
        "/ComponentTypes/SwcM/PpX"
    ]


def test_apply_needs_an_accepted_request(start_server: Starter) -> None:
    service, client = start_server()
    reply = client.post("/api/apply")
    assert (reply.status, reply.error_code) == (409, "nothing-to-apply")
    client.wait_job(
        client.start(
            "/api/dry-run/rows", {"rows": [{"sheet": "Ports", "values": {"port": "reject"}}]}
        )
    )
    reply = client.post("/api/apply")
    assert (reply.status, reply.error_code) == (409, "nothing-to-apply")
    assert "accepted no request" in reply.json()["error"]["message"]
    assert "start_apply" not in service.calls


def test_apply_runs_and_produces_a_report(start_server: Starter) -> None:
    service, client = start_server()
    assert client.ok("/api/report") == {"present": False}
    client.wait_job(client.start("/api/dry-run/rows", {"rows": [PORT_ROW, PORT_ROW]}))
    reply = client.post("/api/apply")
    assert reply.status == 200 and reply.json()["accepted"] == 2
    job = client.wait_job(reply.json()["job_id"])
    assert job["name"] == "apply" and job["summary"]["report_file"] == service.report_file
    report = client.ok("/api/report")
    assert report["present"] is True and report["ok"] is True
    assert report["branch"] == "porttool/apply-20261111-120000"
    assert report["report_file"] == service.report_file
    assert report["counts"] == {"applied": 2} and report["rolled_back"] is False
    assert client.ok("/api/state")["report_present"] is True
    again = client.post("/api/apply")
    assert (again.status, again.error_code) == (409, "nothing-to-apply")
    assert "validate again" in again.json()["error"]["message"]


def test_apply_is_refused_when_stale_or_in_recovery(start_server: Starter) -> None:
    service, client = start_server()
    client.wait_job(client.start("/api/dry-run/rows", {"rows": [PORT_ROW]}))
    service.stale = True
    deadline = time.monotonic() + 5
    while not client.ok("/api/state")["stale"] and time.monotonic() < deadline:
        time.sleep(0.02)
    state = client.ok("/api/state")["validation"]
    assert state["can_apply"] is False and "reload" in " ".join(state["blockers"])
    assert client.post("/api/apply").error_code == "stale"
    service.recovery = True
    while not client.ok("/api/state")["recovery_needed"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert client.post("/api/apply").error_code == "recovery-needed"
    for path, payload in (("/api/load", None), ("/api/dry-run/rows", {"rows": [PORT_ROW]})):
        assert client.post(path, payload).error_code == "recovery-needed"
    assert "start_apply" not in service.calls


def test_rollback_only_when_recovery_is_needed(start_server: Starter) -> None:
    service, client = start_server()
    reply = client.post("/api/rollback")
    assert (reply.status, reply.error_code) == (409, "no-recovery")
    service.recovery = True
    job = client.wait_job(client.start("/api/rollback"))
    assert job["name"] == "rollback" and job["summary"] == {"rolled_back": True}
    assert client.ok("/api/report")["rolled_back"] is True
    deadline = time.monotonic() + 5
    while client.ok("/api/state")["recovery_needed"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert client.ok("/api/state")["recovery_needed"] is False


# --------------------------------------------------------------------------- lists and form


def test_lists_for_the_request_form(client: Client) -> None:
    data = client.ok("/api/lists")
    lists = data["lists"]
    assert data["status"] == "ready"
    assert lists["swcs"] == ["EcuComposition", "SwcA", "SwcB", "SwcM"]
    assert lists["prototypes"] == ["SwcA", "SwcB", "SwcM"]
    assert lists["interfaces"] == ["IfDiag", "IfSpeed", "IfTorque"]
    assert lists["data_types"] == ["uint16"] and lists["base_types"] == ["uint16"]
    assert lists["tasks"] == ["Task_10ms", "Task_Event", "Task_Init"]
    assert lists["compositions"] == ["EcuComposition"]
    assert lists["port_directions"] == ["P", "R", "PR"]
    assert lists["actions"] == ["ADD", "MODIFY", "DELETE"] and lists["yes_no"] == ["Y", "N"]
    assert "implicit read" in lists["access_kinds"] and "periodic" in lists["trigger_kinds"]
    assert "sender-receiver" in lists["interface_kinds"]
    dropdowns = {c.dropdown for columns in COLUMNS.values() for c in columns if c.dropdown}
    assert dropdowns <= set(lists)


def test_lists_details_of_an_swc_and_a_prototype(client: Client) -> None:
    swc = client.ok("/api/lists?swc=SwcB")["swc"]
    assert swc == {
        "name": "SwcB",
        "ports": ["PpDiag", "RpSpeed"],
        "runnables": ["PpDiag_ReadDtc", "RunB_OnSpeed"],
    }
    assert client.ok("/api/lists?swc=SwcB")["lists"] == {}  # no long lists with a detail
    assert client.ok("/api/lists?swc=Nobody")["swc"]["ports"] == []
    prototype = client.ok("/api/lists?prototype=SwcA")["prototype"]
    assert prototype == {"name": "SwcA", "ports": ["PpSpeed", "RpDiag", "RpTorque"]}


def test_form_schema_uses_the_sheet_columns(client: Client) -> None:
    form = client.ok("/api/form")
    assert form["action"] == "ADD"
    kinds = {kind["kind"]: kind for kind in form["kinds"]}
    assert list(kinds) == ["port", "runnable_access", "trigger", "connection", "interface"] + [
        "data_type"
    ]
    sheets = {sheet.value: sheet for sheet in Sheet}
    for kind in kinds.values():
        keys = {column.key for column in COLUMNS[sheets[kind["sheet"]]]}
        assert {field["key"] for field in kind["fields"]} <= keys
        assert not {"row_id", "action", "requester", "comment"} & {f["key"] for f in kind["fields"]}
    port_fields = {field["key"]: field for field in kinds["port"]["fields"]}
    assert list(port_fields) == ["swc", "port", "direction", "interface", "init_value"]
    assert port_fields["direction"]["closed"] is True and port_fields["swc"]["closed"] is False
    assert port_fields["port"]["required"] is True
    children = kinds["interface"]["children"]
    assert children["sheet"] == "InterfaceElements" and children["link_field"] == "interface"
    assert "interface" not in {field["key"] for field in children["fields"]}


# --------------------------------------------------------------------------- error mapping


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ConfigError("porttool.toml is missing."), 400, "PT-E100"),
        (RepoStateError("The working tree is dirty."), 409, "PT-E410"),
        (GitError("git failed."), 500, "PT-E500"),
    ],
)
def test_port_tool_error_becomes_a_json_error(
    start_server: Starter, error: Exception, status: int, code: str
) -> None:
    service, client = start_server()
    service.raise_next = error
    reply = client.post("/api/load")
    assert reply.status == status
    assert reply.json() == {"error": {"code": code, "message": str(error.args[0])}}
    assert client.post("/api/load").status == 200  # the server keeps working


def test_unexpected_error_is_a_generic_500_with_the_traceback_in_the_log(
    start_server: Starter, caplog: pytest.LogCaptureFixture
) -> None:
    service, client = start_server()
    service.raise_next = RuntimeError("secret detail C:\\ecu\\file.arxml")
    with caplog.at_level(logging.ERROR, logger="porttool.gui.server"):
        reply = client.post("/api/load")
    assert reply.status == 500
    error = reply.json()["error"]
    assert error["code"] == "internal" and error["message"].startswith("Unexpected error.")
    assert b"secret detail" not in reply.body and b"Traceback" not in reply.body
    logged = [record for record in caplog.records if record.exc_info]
    assert logged and "RuntimeError" in str(logged[0].exc_info)
    assert client.ok("/api/state")["model_loaded"] is True


# --------------------------------------------------------------------------- background work


def test_pending_table_reads_the_queue_file_of_the_repo(
    start_server: Starter, tmp_path: Path
) -> None:
    queue = PendingQueue(
        entries=(
            PendingEntry(
                operation=AddPort(
                    id="op-1",
                    swc_path="/ComponentTypes/SwcM",
                    name="PpHeld",
                    direction=PortDirection.P,
                    interface_path="/PortInterfaces/IfSpeed",
                    requester="Omar",
                ),
                state=PendingState.ON_HOLD,
                created_utc="2026-11-01T08:00:00Z",
                depends_on=("/PortInterfaces/IfNew",),
                note="waits for the interface",
            ),
        )
    )
    (tmp_path / "porttool").mkdir()
    (tmp_path / "porttool" / "pending.json").write_text(queue.to_json(), encoding="utf-8")
    _, client = start_server(repo=tmp_path)
    client.wait_tables()
    page = client.ok("/api/tables/pending")
    assert page["rows"] == [
        [
            "on hold",
            "add_port",
            "/ComponentTypes/SwcM/PpHeld",
            "Omar",
            "2026-11-01T08:00:00Z",
            "",
            "/PortInterfaces/IfNew",
            "waits for the interface",
        ]
    ]
    assert "note" not in page
    client.wait_job(client.start("/api/dry-run/rows", {"rows": [PORT_ROW]}))
    assert client.ok("/api/tables/pending")["total"] == 0  # the dry run's queue replaces it


def test_unreadable_pending_file_is_reported_not_fatal(
    start_server: Starter, tmp_path: Path
) -> None:
    (tmp_path / "porttool").mkdir()
    (tmp_path / "porttool" / "pending.json").write_text("{ not json", encoding="utf-8")
    _, client = start_server(repo=tmp_path)
    client.wait_tables()
    page = client.ok("/api/tables/pending")
    assert page["total"] == 0 and "could not be read" in page["note"]
    assert client.ok("/api/tables/ports")["total"] == 6


def test_failing_stale_check_is_logged_once_and_the_server_keeps_working(
    start_server: Starter, caplog: pytest.LogCaptureFixture
) -> None:
    service, client = start_server()
    with caplog.at_level(logging.ERROR, logger="porttool.gui.server"):
        service.stale_error = OSError("the network drive is gone")
        time.sleep(0.6)  # several check intervals of 0.1 s
        state = client.ok("/api/state")
    assert state["stale"] is False and state["model_loaded"] is True
    failures = [r for r in caplog.records if "check for changed files failed" in r.getMessage()]
    assert len(failures) == 1 and failures[0].exc_info is not None
    service.stale_error = None
    service.stale = True
    deadline = time.monotonic() + 5
    while not client.ok("/api/state")["stale"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert client.ok("/api/state")["stale"] is True


def test_tables_that_cannot_be_prepared_report_an_error(
    start_server: Starter, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args: object) -> None:
        raise RuntimeError("row builder bug")

    monkeypatch.setattr("porttool.gui.server.build_tables", broken)
    with caplog.at_level(logging.ERROR, logger="porttool.gui.server"):
        _, client = start_server()
        deadline = time.monotonic() + 5
        while client.ok("/api/state")["tables"] == "preparing" and time.monotonic() < deadline:
            time.sleep(0.02)
    assert client.ok("/api/state")["tables"] == "failed"
    for path in ("/api/tables", "/api/tables/ports", "/api/lists"):
        reply = client.get(path)
        assert (reply.status, reply.error_code) == (500, "tables-failed"), path
        assert b"row builder bug" not in reply.body
    assert sum("could not be prepared" in r.getMessage() for r in caplog.records) == 1
    assert client.ok("/api/form")["action"] == "ADD"


def test_requests_on_an_open_connection_are_refused_after_close() -> None:
    service = FakeService()
    handle = serve(service, open_browser=False)
    headers = {"Host": f"127.0.0.1:{handle.port}", TOKEN_HEADER: handle.token}
    connection = http.client.HTTPConnection("127.0.0.1", handle.port, timeout=10)
    try:
        connection.request("GET", "/api/state", headers=headers)
        first = connection.getresponse()
        first.read()
        assert first.status == 200 and first.getheader("Connection") != "close"
        handle.close()
        try:
            connection.request("GET", "/api/state", headers=headers)
            second = connection.getresponse()
            body = second.read()
        except (http.client.HTTPException, OSError):
            return  # the connection was dropped: also fine
        assert second.status == 503 and b'"stopped"' in body
        assert second.getheader("Connection") == "close"
    finally:
        connection.close()
        service.close()


def test_actions_that_need_a_loaded_model_or_an_idle_service(
    start_server: Starter, tmp_path: Path
) -> None:
    service, client = start_server(loaded=False, repo=tmp_path)
    for path, payload in (
        ("/api/export", {"target": str(tmp_path / "x.xlsx")}),
        ("/api/dry-run/rows", {"rows": [PORT_ROW]}),
        ("/api/apply", None),
    ):
        reply = client.post(path, payload)
        assert (reply.status, reply.error_code) == (409, "no-model"), path
    service.hang_next = True
    job_id = client.start("/api/load")
    reply = client.post("/api/open-repo", {"repo": str(tmp_path)})
    assert (reply.status, reply.error_code) == (409, "job-running")
    assert service.calls.count("open_repo") == 0
    client.post(f"/api/jobs/{job_id}/cancel")
    client.wait_job(job_id)


def test_upload_is_removed_when_the_service_refuses_the_dry_run(start_server: Starter) -> None:
    service, client = start_server()
    service.raise_next = RepoStateError("The working tree has uncommitted changes.")
    content_type, body = multipart([("a.xlsx", xlsx_bytes())])
    reply = client.post("/api/dry-run/upload", body=body, headers={"Content-Type": content_type})
    assert (reply.status, reply.error_code) == (409, "PT-E410")
    assert not list(client.handle.temp_dir.rglob("*.xlsx"))
    assert client.ok("/api/jobs")["jobs"] == []
