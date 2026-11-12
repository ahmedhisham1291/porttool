"""One named test per corner case of the GUI task.

Corner cases covered in other files:
* wrong token                       test_server.py::test_api_call_with_wrong_token_is_refused
* service raises PortToolError      test_server.py::test_port_tool_error_becomes_a_json_error
* service raises something else     test_server.py::
                                    test_unexpected_error_is_a_generic_500_with_the_traceback_in_the_log
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Route, expect

from porttool.core.models import Model
from porttool.gui import GuiError, serve
from porttool.gui.tables import build_tables
from tests.unit.gui.conftest import Client, Starter, TabOpener, multipart, xlsx_bytes
from tests.unit.gui.fake_service import (
    ARABIC_NAME,
    HEBREW_NAME,
    LONG_NAME,
    SCRIPT_NAME,
    FakeService,
    build_large_model,
    build_tricky_model,
)
from tests.unit.gui.test_browser import (
    HTTP_ERROR,
    add_port_request,
    body_rows,
    cells,
    count_text,
    open_requests,
    validate_one_port,
)
from tests.unit.gui.test_server import PORT_ROW, TABLE_NAMES, reference_query

# --------------------------------------------------------------------------- empty model


def test_empty_model_through_the_api(start_server: Starter) -> None:
    _, client = start_server(model=Model())
    listed = client.wait_tables()
    assert listed["status"] == "ready"
    assert [(t["name"], t["rows"]) for t in listed["tables"]] == [(n, 0) for n in TABLE_NAMES]
    for name in TABLE_NAMES:
        page = client.ok(f"/api/tables/{name}?q=x")
        assert (page["status"], page["total"], page["filtered"], page["rows"]) == (
            "ready",
            0,
            0,
            [],
        )
    state = client.ok("/api/state")
    assert state["model_loaded"] is True and set(state["model"]["counts"].values()) == {0}
    lists = client.ok("/api/lists")["lists"]
    assert lists["swcs"] == [] and lists["interfaces"] == [] and lists["port_directions"]
    assert client.post("/api/dry-run/rows", {"rows": [PORT_ROW]}).status == 200


def test_empty_model_in_the_page(start_server: Starter, open_tab: TabOpener) -> None:
    _, client = start_server(model=Model())
    page = open_tab(client).page
    expect(count_text(page, "ports")).to_have_text("0 of 0 rows")
    expect(page.locator("#panel-ports .vt-empty")).to_have_text("This table is empty.")
    expect(page.locator("#model-counts")).to_contain_text("0 SWCs")
    page.click("#tab-connections")
    expect(page.locator("#panel-connections .vt-empty")).to_have_text("This table is empty.")
    open_requests(page)
    expect(page.locator("#f-swc")).to_be_visible()


# --------------------------------------------------------------------------- 100,000 rows


def test_table_with_100000_rows_through_the_api(start_server: Starter) -> None:
    model = build_large_model(swcs=2000, ports_per_swc=50, connectors=60000)
    rows = build_tables(model, None)["ports"].rows
    assert len(rows) == 100000
    _, client = start_server(model=model)
    started = time.perf_counter()
    client.wait_tables(timeout=120)
    prepare_s = time.perf_counter() - started
    timings: list[str] = [f"tables prepared in {prepare_s:.2f} s"]
    queries: list[tuple[str, dict[str, Any]]] = [
        ("", {}),
        ("offset=99800", {"offset": 99800}),
        ("offset=99900&limit=500", {"offset": 99900, "limit": 500}),
        ("sort=port&dir=desc&offset=50000", {"sort": "port", "desc": True, "offset": 50000}),
        ("q=swc_01999&sort=interface", {"q": "swc_01999", "sort": "interface"}),
        (
            "f.port=rp_04&f.swc=swc_000&sort=partner_count&dir=desc&limit=500",
            {"filters": {"port": "rp_04", "swc": "swc_000"}, "sort": "partner_count", "desc": True}
            | {"limit": 500},
        ),
        ("only=unconnected&offset=30000", {"only": "unconnected", "offset": 30000}),
    ]
    for query, expected in queries:
        started = time.perf_counter()
        first = client.ok(f"/api/tables/ports?{query}")
        first_ms = (time.perf_counter() - started) * 1000.0
        started = time.perf_counter()
        again = client.ok(f"/api/tables/ports?{query}")
        warm_ms = (time.perf_counter() - started) * 1000.0
        want_rows, total, filtered = reference_query(rows, "ports", **expected)
        assert (first["total"], first["filtered"]) == (total, filtered) == (100000, filtered)
        assert first["rows"] == want_rows and again["rows"] == want_rows
        assert warm_ms < 200, f"{query}: {warm_ms:.0f} ms when warm"
        timings.append(f"{first_ms:7.1f} ms first {warm_ms:6.1f} ms warm  ports?{query}")
    print("\n".join(timings))


# --------------------------------------------------------------------------- stale answers


def test_rapid_filter_typing_shows_only_the_last_response(
    start_server: Starter, open_tab: TabOpener
) -> None:
    _, client = start_server()
    page = open_tab(client).page
    expect(body_rows(page, "ports")).to_have_count(6)
    searches: list[str] = []
    held: list[Route] = []

    def handler(route: Route) -> None:
        url = route.request.url
        if "q=" in url:
            searches.append(url)
        if "q=swca" in url:
            held.append(route)  # this answer arrives late, after the newer one
        else:
            route.continue_()

    page.route("**/api/tables/ports?*", handler)
    page.locator("#search-ports").press_sequentially("swca", delay=20)
    page.wait_for_timeout(400)  # past the 250 ms pause: one request for the whole word
    assert len(searches) == 1 and "q=swca" in searches[0], searches
    page.fill("#search-ports", "swcb")
    expect(count_text(page, "ports")).to_have_text("2 of 6 rows")
    assert len(held) == 1
    held[0].continue_()  # the older answer (3 SwcA rows) arrives now
    page.wait_for_timeout(400)
    expect(count_text(page, "ports")).to_have_text("2 of 6 rows")
    assert [cells(page, "ports", n)[0] for n in range(2)] == ["SwcB", "SwcB"]
    assert body_rows(page, "ports").count() == 2


def test_sort_during_load_shows_the_sorted_rows(start_server: Starter, open_tab: TabOpener) -> None:
    _, client = start_server()
    held: list[Route] = []

    def handler(route: Route) -> None:
        if "sort=" in route.request.url:
            route.continue_()
        else:
            held.append(route)  # the first, unsorted load stays unanswered for now

    tab = open_tab(client)
    page = tab.page
    expect(body_rows(page, "ports")).to_have_count(6)
    page.route("**/api/tables/ports?*", handler)
    page.locator("#panel-ports").get_by_role("button", name="Clear filters").click()  # new load
    expect(count_text(page, "ports")).to_have_text("Loading…")
    header = page.locator("#panel-ports").get_by_role("columnheader", name="Port", exact=True)
    header.get_by_role("button").click()
    header.get_by_role("button").click()
    expect(header).to_have_attribute("aria-sort", "descending")
    expect(body_rows(page, "ports").first.locator(".vt-cell").nth(1)).to_have_text("RpTorque")
    assert len(held) == 1
    held[0].continue_()
    page.wait_for_timeout(400)
    ports = [cells(page, "ports", n)[1] for n in range(6)]
    assert ports == sorted(ports, reverse=True) and ports[0] == "RpTorque"
    expect(count_text(page, "ports")).to_have_text("6 of 6 rows")


# --------------------------------------------------------------------------- apply, cancel


def test_double_click_on_apply_starts_one_job(start_server: Starter, open_tab: TabOpener) -> None:
    service, client = start_server(steps=4, step_s=0.1)
    page = open_tab(client).page
    validate_one_port(page)
    page.dblclick("#btn-apply")
    expect(page.locator("#confirm-dialog")).to_be_visible()
    page.dblclick("#confirm-ok")
    expect(page.locator("#apply-result")).to_contain_text("Apply finished.")
    assert service.calls.count("start_apply") == 1
    assert [job["name"] for job in client.ok("/api/jobs")["jobs"]].count("apply") == 1
    expect(page.locator("#messages .msg-error")).to_have_count(0)


def test_two_apply_calls_at_once_start_one_job(start_server: Starter) -> None:
    service, client = start_server(steps=3, step_s=0.1)
    client.wait_job(client.start("/api/dry-run/rows", {"rows": [PORT_ROW]}))
    gate = threading.Barrier(4)
    statuses: list[int] = []

    def click() -> None:
        gate.wait(5)
        statuses.append(client.post("/api/apply").status)

    threads = [threading.Thread(target=click) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert sorted(statuses) == [200, 409, 409, 409]
    assert service.calls.count("start_apply") == 1
    service.wait_idle()
    assert client.post("/api/apply").status == 409  # still one: validate again first


def test_cancel_twice(start_server: Starter, open_tab: TabOpener) -> None:
    service, client = start_server()
    service.hang_next = True
    job_id = client.start("/api/load")
    first = client.post(f"/api/jobs/{job_id}/cancel")
    second = client.post(f"/api/jobs/{job_id}/cancel")
    assert (first.status, first.json()["cancelled"]) == (200, True)
    assert (second.status, second.json()["cancelled"]) == (200, False)
    assert client.wait_job(job_id)["state"] == "cancelled"
    third = client.post(f"/api/jobs/{job_id}/cancel")
    assert (third.status, third.json()["cancelled"]) == (200, False)

    page = open_tab(client).page
    expect(body_rows(page, "ports")).to_have_count(6)
    service.hang_next = True
    service.calls.clear()
    page.click("#btn-reload")
    expect(page.locator("#job-strip")).to_be_visible()
    page.dblclick("#btn-cancel-job")
    expect(page.locator("#job-strip")).to_be_hidden()
    assert service.calls.count("cancel") == 1
    expect(page.locator("#messages .msg-error")).to_have_count(0)


def test_closing_the_server_during_a_job() -> None:
    service = FakeService()
    handle = serve(service, open_browser=False)
    client = Client(handle)
    content_type, body = multipart([("a.xlsx", xlsx_bytes())])
    service.hang_next = True
    reply = client.post("/api/dry-run/upload", body=body, headers={"Content-Type": content_type})
    job_id = reply.json()["job_id"]
    assert client.ok(f"/api/jobs/{job_id}")["active"] is True
    temp_dir = handle.temp_dir
    assert list(temp_dir.rglob("*.xlsx"))
    started = time.perf_counter()
    handle.close()
    assert time.perf_counter() - started < 3.0, "close() must not wait for the job"
    assert handle.closed and not temp_dir.exists()
    handle.close()  # a second close is harmless
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", handle.port), timeout=2):
        pass
    running = service.job(job_id)
    assert running is not None and running.state == "running"  # the server never ends jobs
    service.close()
    service.wait_idle()
    ended = service.job(job_id)
    assert ended is not None and ended.state == "cancelled"


# --------------------------------------------------------------------------- model swap


def test_model_swapped_while_the_page_is_open(
    start_server: Starter, open_tab: TabOpener, large_model: Model
) -> None:
    service, client = start_server()
    page = open_tab(client).page
    expect(count_text(page, "ports")).to_have_text("6 of 6 rows")
    first_hash = client.ok("/api/tables/ports")["model_hash"]
    page.fill("#search-ports", "pp")
    expect(count_text(page, "ports")).to_have_text("3 of 6 rows")

    service.stale = True
    expect(page.locator("#banner-stale")).to_be_visible()
    service.next_model = large_model
    page.click("#btn-reload-stale")
    expect(page.locator("#banner-stale")).to_be_hidden()  # the new model is fresh
    expect(count_text(page, "ports")).to_have_text("4,000 of 20,000 rows")  # filter kept
    expect(page.locator("#tab-ports .tab-count")).to_have_text("20,000")
    expect(page.locator("#tab-connections .tab-count")).to_have_text("15,000")
    expect(page.locator("#model-counts")).to_contain_text("20,000 ports")
    assert cells(page, "ports")[0] == "Swc_00000"
    answer = client.ok("/api/tables/ports")
    assert answer["model_hash"] != first_hash and answer["total"] == 20000

    service.set_model(build_tricky_model())  # swapped without a job, e.g. by the command line
    expect(count_text(page, "ports")).to_have_text("1 of 6 rows")  # "pp" finds PpVeryLong...
    expect(page.locator("#tab-ports .tab-count")).to_have_text("6")
    page.click("#tab-components")
    expect(body_rows(page, "components")).to_have_count(1)
    assert cells(page, "components")[0] == "SwcTricky"


# --------------------------------------------------------------------------- hostile names


def test_long_non_ascii_rtl_and_script_names_render_safely(
    start_server: Starter, open_tab: TabOpener
) -> None:
    service, client = start_server(model=build_tricky_model())
    page = open_tab(client).page
    expect(body_rows(page, "ports")).to_have_count(6)
    names = [cells(page, "ports", n)[1] for n in range(6)]
    assert set(names) == {SCRIPT_NAME, LONG_NAME, ARABIC_NAME, HEBREW_NAME, "Größe_°C", "a&b\"c'd"}
    assert cells(page, "ports", 0)[3] == "<b>IfBold</b>"
    assert page.evaluate("window.hacked") is None
    assert page.locator("#panels script, #panels b, #panels img").count() == 0

    heights = page.locator("#panel-ports .vt-body .vt-row").evaluate_all(
        "rows => rows.map(row => row.getBoundingClientRect().height)"
    )
    assert heights == [28] * 6, "a long name must not make a row taller"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    long_cell = page.locator("#panel-ports .vt-cell", has_text=LONG_NAME).first
    assert long_cell.get_attribute("title") == LONG_NAME  # the full name on hover
    assert long_cell.evaluate("cell => cell.scrollWidth > cell.clientWidth")  # cut with ...
    assert long_cell.evaluate("cell => cell.getBoundingClientRect().width") <= 181
    arabic = page.locator("#panel-ports .vt-cell", has_text=ARABIC_NAME).first
    assert arabic.evaluate("cell => getComputedStyle(cell).direction") == "rtl"
    latin = page.locator("#panel-ports .vt-cell", has_text="Größe_°C").first
    assert latin.evaluate("cell => getComputedStyle(cell).direction") == "ltr"

    page.fill("#search-ports", "<script>")
    expect(count_text(page, "ports")).to_have_text("1 of 6 rows")
    page.fill("#search-ports", ARABIC_NAME)
    expect(count_text(page, "ports")).to_have_text("1 of 6 rows")

    open_requests(page)
    hostile = "<img src=x onerror=window.hacked=1>reject"
    add_port_request(page, "SwcTricky", hostile)
    add_port_request(page, "SwcTricky", ARABIC_NAME)
    expect(page.locator("#form-list tbody tr").first).to_contain_text(hostile)
    page.click("#btn-validate-form")
    rows = page.locator("#results-table tbody tr")
    expect(rows).to_have_count(2)
    expect(rows.first).to_contain_text(f"The name '{hostile}' does not follow the naming rule.")
    expect(page.locator("#plan-preview")).to_contain_text(ARABIC_NAME)
    page.wait_for_timeout(200)
    assert page.evaluate("window.hacked") is None
    assert page.locator("#panel-requests img, #panel-requests script").count() == 0
    assert service.dry_run_rows[0].values["port"] == hostile


# --------------------------------------------------------------------------- port, uploads


def test_port_already_in_use() -> None:
    first = serve(FakeService(loaded=False), open_browser=False)
    try:
        service = FakeService(loaded=False)
        with pytest.raises(GuiError) as caught:
            serve(service, port=first.port, open_browser=False)
        assert caught.value.code == "PT-E700"
        assert f"127.0.0.1:{first.port}" in caught.value.user_message
        assert "--port" in caught.value.user_message
        assert Client(first).ok("/api/state")["config_present"] is False  # the first one lives
    finally:
        first.close()


def test_port_taken_by_another_program_is_reported() -> None:
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen(1)
        with pytest.raises(GuiError, match="may be in use"):
            serve(FakeService(loaded=False), port=other.getsockname()[1], open_browser=False)


def test_upload_of_a_non_xlsx_file_is_refused(start_server: Starter) -> None:
    service, client = start_server()
    real = xlsx_bytes()
    cases = [
        [("notes.txt", b"hello")],
        [("renamed.xlsx", b"this is plain text, not a workbook")],
        [("macro.xlsm", real)],
        [("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)],
        [("good.xlsx", real), ("bad.xlsx", b"PK\x03\x04 broken zip")],
        [("empty.xlsx", b"")],
    ]
    for files in cases:
        content_type, body = multipart(files)
        reply = client.post(
            "/api/dry-run/upload", body=body, headers={"Content-Type": content_type}
        )
        assert (reply.status, reply.error_code) == (400, "not-xlsx"), files[0][0]
        assert files[-1][0] in reply.json()["error"]["message"]
    raw = client.post("/api/dry-run/upload?name=notes.txt", body=b"hello")
    assert (raw.status, raw.error_code) == (400, "not-xlsx")
    assert "start_dry_run" not in service.calls
    assert not list(client.handle.temp_dir.rglob("*")), "nothing may be saved"
    assert client.ok("/api/jobs")["jobs"] == []


def test_upload_of_a_non_xlsx_file_is_refused_in_the_page(
    start_server: Starter, open_tab: TabOpener, tmp_path: Path
) -> None:
    service, client = start_server()
    tab = open_tab(client)
    page = tab.page
    open_requests(page)
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    page.set_input_files("#file-input", [str(notes)])
    expect(page.locator("#messages .msg-error")).to_contain_text(
        "'notes.txt' is not an .xlsx workbook."
    )
    expect(page.locator("#file-list li")).to_have_count(0)
    expect(page.locator("#btn-validate-files")).to_be_disabled()

    fake = tmp_path / "renamed.xlsx"
    fake.write_text("this is plain text")
    tab.allow(HTTP_ERROR)
    page.set_input_files("#file-input", [str(fake)])
    page.click("#btn-validate-files")
    expect(page.locator("#messages")).to_contain_text("[not-xlsx] 'renamed.xlsx' is not a valid")
    expect(page.locator("#job-strip")).to_be_hidden()
    assert "start_dry_run" not in service.calls
