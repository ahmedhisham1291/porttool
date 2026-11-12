"""The page in headless Chrome (Playwright) against the fake service.

Every test fails on a JavaScript console error or page error (see ``open_tab``). The
tests are skipped when Chrome cannot be started.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest
from playwright.sync_api import Locator, Page, expect

from porttool.core.errors import ConfigError
from porttool.core.models import Model
from tests.unit.gui.conftest import Starter, TabOpener, xlsx_bytes

HTTP_ERROR = r"Failed to load resource: the server responded with a status of"


def body_rows(page: Page, table: str) -> Locator:
    return page.locator(f"#panel-{table} .vt-body .vt-row:not(.loading)")


def cells(page: Page, table: str, row: int = 0) -> list[str]:
    return body_rows(page, table).nth(row).locator(".vt-cell").all_inner_texts()


def count_text(page: Page, table: str) -> Locator:
    return page.locator(f"#panel-{table} .table-count")


def column_filter(page: Page, table: str, header: str) -> Locator:
    return page.locator(f"#panel-{table}").get_by_label(f"Filter {header}", exact=True)


def open_requests(page: Page) -> None:
    page.click("#tab-requests")
    expect(page.locator("#panel-requests")).to_be_visible()
    expect(page.locator("#form-kind option")).to_have_count(6)


def add_port_request(page: Page, swc: str, port: str) -> None:
    page.select_option("#form-kind", "port")
    page.fill("#f-swc", swc)
    page.fill("#f-port", port)
    page.select_option("#f-direction", "P")
    page.fill("#f-interface", "IfSpeed")
    page.fill("#f-requester", "Mona")
    page.click("#btn-add-request")


def validate_one_port(page: Page) -> None:
    open_requests(page)
    add_port_request(page, "SwcA", "PpNew")
    page.click("#btn-validate-form")
    expect(page.locator("#results-table tbody tr")).to_have_count(1)
    expect(page.locator("#btn-apply")).to_be_enabled()


# --------------------------------------------------------------------------- loading, tables


def test_page_loads_without_console_errors(start_server: Starter, open_tab: TabOpener) -> None:
    _, client = start_server()
    tab = open_tab(client)
    page = tab.page
    expect(body_rows(page, "ports")).to_have_count(6)
    assert page.title() == "PortTool"
    assert "token" not in page.url, "the token must leave the address bar"
    expect(page.locator("#repo-path")).to_have_text(str(Path("C:/ecu/repo")))
    expect(page.locator("#model-counts")).to_contain_text("4 SWCs")
    expect(page.locator("#model-counts")).to_contain_text("6 ports")
    expect(page.locator("#view-setup")).to_be_hidden()
    expect(page.locator("#banner-stale")).to_be_hidden()
    expect(page.locator("#banner-recovery")).to_be_hidden()
    expect(page.locator("#job-strip")).to_be_hidden()
    expect(page.locator("#fatal")).to_be_hidden()
    assert tab.problems == []


def test_page_makes_no_external_request(start_server: Starter, open_tab: TabOpener) -> None:
    _, client = start_server()
    tab = open_tab(client)
    urls: list[str] = []
    tab.page.on("request", lambda request: urls.append(request.url))
    tab.page.reload()  # the reload drops the token: only static files are fetched again
    tab.page.goto(client.handle.url)
    expect(body_rows(tab.page, "ports")).to_have_count(6)
    assert urls and all(url.startswith(client.handle.base_url) for url in urls), urls


def test_page_without_token_explains_what_to_do(start_server: Starter, open_tab: TabOpener) -> None:
    _, client = start_server()
    tab = open_tab(client)
    tab.page.goto(client.handle.base_url)
    expect(tab.page.locator("#start-text")).to_contain_text("without its session token")
    expect(tab.page.locator("#view-main")).to_be_hidden()


def test_every_tab_shows_its_data(start_server: Starter, open_tab: TabOpener) -> None:
    _, client = start_server()
    page = open_tab(client).page
    expected = {
        "components": (4, "EcuComposition"),
        "ports": (6, "PpSpeed"),
        "runnable_access": (3, "implicit write"),
        "triggers": (3, "Task_10ms"),
        "connections": (2, "SwcA_PpSpeed_SwcB_RpSpeed"),
        "unconnected": (2, "SwcM.PpTorque"),
        "interfaces": (3, "client-server"),
        "interface_elements": (3, "ReadDtc"),
        "data_types": (1, "uint16"),
    }
    for table, (rows, text) in expected.items():
        page.click(f"#tab-{table}")
        expect(page.locator(f"#tab-{table}")).to_have_attribute("aria-selected", "true")
        expect(body_rows(page, table)).to_have_count(rows)
        expect(count_text(page, table)).to_have_text(f"{rows} of {rows} rows")
        expect(page.locator(f"#panel-{table} .vt-body")).to_contain_text(text)
        expect(page.locator(f"#tab-{table} .tab-count")).to_have_text(str(rows))
    page.click("#tab-pending")
    expect(page.locator("#panel-pending .vt-empty")).to_have_text("This table is empty.")
    page.click("#tab-requests")
    expect(page.locator("#h-export")).to_be_visible()
    expect(page.locator("#panel-ports")).to_be_hidden()


def test_typing_a_filter_narrows_the_rows(start_server: Starter, open_tab: TabOpener) -> None:
    _, client = start_server()
    page = open_tab(client).page
    expect(body_rows(page, "ports")).to_have_count(6)
    column_filter(page, "ports", "SWC").fill("swcb")
    expect(body_rows(page, "ports")).to_have_count(2)
    expect(count_text(page, "ports")).to_have_text("2 of 6 rows")
    assert {cells(page, "ports", n)[0] for n in range(2)} == {"SwcB"}
    column_filter(page, "ports", "Direction").fill("p")
    expect(body_rows(page, "ports")).to_have_count(1)
    assert cells(page, "ports")[:3] == ["SwcB", "PpDiag", "P"]
    page.locator("#panel-ports").get_by_role("button", name="Clear filters").click()
    expect(body_rows(page, "ports")).to_have_count(6)
    page.fill("#search-ports", "torque")
    expect(body_rows(page, "ports")).to_have_count(2)
    page.fill("#search-ports", "no such port")
    expect(page.locator("#panel-ports .vt-empty")).to_have_text("No row matches the filters.")
    expect(count_text(page, "ports")).to_have_text("0 of 6 rows")
    page.fill("#search-ports", "")
    page.locator("#panel-ports").get_by_label("Only unconnected").check()
    expect(body_rows(page, "ports")).to_have_count(2)
    assert sorted(cells(page, "ports", n)[1] for n in range(2)) == ["PpTorque", "RpTorque"]


def test_sorting_by_clicking_a_header(start_server: Starter, open_tab: TabOpener) -> None:
    _, client = start_server()
    page = open_tab(client).page
    expect(body_rows(page, "ports")).to_have_count(6)
    header = page.locator("#panel-ports").get_by_role("columnheader", name="Port", exact=True)
    header.get_by_role("button").click()
    expect(header).to_have_attribute("aria-sort", "ascending")
    expect(body_rows(page, "ports").first.locator(".vt-cell").nth(1)).to_have_text("PpDiag")
    ports = [cells(page, "ports", n)[1] for n in range(6)]
    assert ports == sorted(ports)
    header.get_by_role("button").click()
    expect(header).to_have_attribute("aria-sort", "descending")
    expect(body_rows(page, "ports").first.locator(".vt-cell").nth(1)).to_have_text("RpTorque")
    assert [cells(page, "ports", n)[1] for n in range(6)] == sorted(ports, reverse=True)
    header.get_by_role("button").click()
    expect(header).to_have_attribute("aria-sort", "none")
    expect(body_rows(page, "ports").first.locator(".vt-cell").nth(1)).to_have_text("PpSpeed")


def test_scrolling_20000_rows_fetches_more_pages_and_stays_responsive(
    start_server: Starter, open_tab: TabOpener, large_model: Model
) -> None:
    _, client = start_server(model=large_model)
    client.wait_tables()
    tab = open_tab(client)
    page = tab.page
    fetched: list[str] = []
    page.on("request", lambda r: fetched.append(r.url) if "/api/tables/ports?" in r.url else None)
    expect(count_text(page, "ports")).to_have_text("20,000 of 20,000 rows")
    scroller = page.locator("#panel-ports .vt-scroll")
    assert cells(page, "ports")[:2] == ["Swc_00000", "Pp_000"]
    drawn = page.locator("#panel-ports .vt-body .vt-row").count()
    assert 10 < drawn < 80, f"only the visible rows may exist, not {drawn}"
    assert scroller.evaluate("el => el.scrollHeight") > 20000 * 28

    started = time.perf_counter()
    scroller.evaluate("el => { el.scrollTop = el.scrollHeight; }")
    last = page.locator('#panel-ports .vt-row[data-index="19999"]:not(.loading)')
    expect(last).to_be_visible()
    to_bottom_s = time.perf_counter() - started
    assert last.locator(".vt-cell").all_inner_texts()[:2] == ["Swc_00399", "Rp_049"]
    assert to_bottom_s < 3.0, f"jump to the last row took {to_bottom_s:.2f} s"

    started = time.perf_counter()
    scroller.evaluate("el => { el.scrollTop = 10000 * 28; }")
    middle = page.locator('#panel-ports .vt-row[data-index="10005"]:not(.loading)')
    expect(middle).to_be_visible()
    assert time.perf_counter() - started < 3.0
    assert middle.locator(".vt-cell").all_inner_texts()[:2] == ["Swc_00200", "Pp_005"]

    for step in range(1, 30):  # scroll in steps, as a mouse wheel does
        scroller.evaluate("(el, top) => { el.scrollTop = top; }", step * 2500)
    expect(page.locator('#panel-ports .vt-row[data-index="2600"]:not(.loading)')).to_be_visible()
    assert page.locator("#panel-ports .vt-body .vt-row").count() < 80
    offsets = {re.search(r"offset=(\d+)", url).group(1) for url in fetched}  # type: ignore[union-attr]
    assert {"0", "19800", "10000", "2600"} <= offsets
    assert all("limit=200" in url for url in fetched)
    print(f"jump to the last of 20,000 rows: {to_bottom_s * 1000:.0f} ms, {len(fetched)} fetches")


def test_keyboard_scrolls_the_table_and_ctrl_f_focuses_the_search(
    start_server: Starter, open_tab: TabOpener, large_model: Model
) -> None:
    _, client = start_server(model=large_model)
    client.wait_tables()
    page = open_tab(client).page
    expect(count_text(page, "ports")).to_have_text("20,000 of 20,000 rows")
    scroller = page.locator("#panel-ports .vt-scroll")
    scroller.focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    assert scroller.evaluate("el => el.scrollTop") == 56
    page.keyboard.press("PageDown")
    after_page = scroller.evaluate("el => el.scrollTop")
    assert after_page > 300
    page.keyboard.press("PageUp")
    assert scroller.evaluate("el => el.scrollTop") == 56
    page.keyboard.press("End")
    expect(page.locator('#panel-ports .vt-row[data-index="19999"]:not(.loading)')).to_be_visible()
    page.keyboard.press("Home")
    assert scroller.evaluate("el => el.scrollTop") == 0
    page.keyboard.press("Control+f")
    expect(page.locator("#search-ports")).to_be_focused()
    page.keyboard.type("swc_00123")
    expect(count_text(page, "ports")).to_have_text("50 of 20,000 rows")


# --------------------------------------------------------------------------- setup, jobs


def test_setup_view_opens_a_repo_and_loads_it(
    start_server: Starter, open_tab: TabOpener, tmp_path: Path
) -> None:
    service, client = start_server(loaded=False)
    tab = open_tab(client)
    page = tab.page
    expect(page.locator("#view-setup")).to_be_visible()
    expect(page.locator("#view-main")).to_be_hidden()
    expect(page.locator("#repo-path")).to_have_text("No repo open")
    tab.allow(HTTP_ERROR)
    page.fill("#repo-input", str(tmp_path / "missing"))
    page.click("#btn-open")
    expect(page.locator("#messages .msg-error")).to_contain_text("[PT-E100]")
    expect(page.locator("#messages .msg-error")).to_contain_text("does not exist")
    page.fill("#repo-input", str(tmp_path))
    page.click("#btn-open")
    expect(page.locator("#view-main")).to_be_visible()
    expect(page.locator("#repo-path")).to_have_text(str(tmp_path))
    expect(body_rows(page, "ports")).to_have_count(6)  # the load was started by the page
    assert service.calls.count("start_load") == 1


def test_reload_shows_the_job_strip_with_progress(
    start_server: Starter, open_tab: TabOpener
) -> None:
    service, client = start_server(steps=5, step_s=0.25)
    page = open_tab(client).page
    expect(body_rows(page, "ports")).to_have_count(6)
    page.click("#btn-reload")
    strip = page.locator("#job-strip")
    expect(strip).to_be_visible()
    expect(page.locator("#job-name")).to_have_text("Loading the repo")
    expect(page.locator("#btn-reload")).to_be_disabled()
    expect(page.locator("#job-message")).to_contain_text(re.compile(r"step [2-5] of 5"))
    assert int(page.locator("#job-progress").get_attribute("value") or "0") >= 40
    expect(page.locator("#job-elapsed")).to_have_text(re.compile(r"^\d+ s$"))
    expect(strip).to_be_hidden()
    expect(page.locator("#btn-reload")).to_be_enabled()
    assert service.calls.count("start_load") == 1


def test_cancel_a_running_job(start_server: Starter, open_tab: TabOpener) -> None:
    service, client = start_server()
    page = open_tab(client).page
    expect(body_rows(page, "ports")).to_have_count(6)
    service.hang_next = True
    page.click("#btn-reload")
    expect(page.locator("#job-strip")).to_be_visible()
    page.click("#btn-cancel-job")
    expect(page.locator("#job-strip")).to_be_hidden()
    expect(page.locator("#messages .msg-info")).to_have_text(
        re.compile("Loading the repo was cancelled.")
    )
    assert client.ok("/api/jobs")["jobs"][0]["state"] == "cancelled"


def test_stale_banner_and_recovery_banner(start_server: Starter, open_tab: TabOpener) -> None:
    service, client = start_server()
    page = open_tab(client).page
    expect(body_rows(page, "ports")).to_have_count(6)
    service.stale = True
    stale = page.locator("#banner-stale")
    expect(stale).to_be_visible()
    expect(stale).to_contain_text("Files changed on disk: reload")
    page.click("#btn-reload-stale")
    expect(stale).to_be_hidden()
    assert service.calls.count("start_load") == 1

    service.recovery = True
    recovery = page.locator("#banner-recovery")
    expect(recovery).to_be_visible()
    expect(page.locator("#btn-reload")).to_be_disabled()
    page.click("#btn-rollback")
    expect(page.locator("#confirm-dialog")).to_be_visible()
    page.click("#confirm-ok")
    expect(recovery).to_be_hidden()
    assert service.calls.count("start_rollback") == 1
    expect(page.locator("#messages")).to_contain_text("Rolling back finished.")


def test_an_api_error_is_shown_with_code_and_text(
    start_server: Starter, open_tab: TabOpener
) -> None:
    service, client = start_server()
    tab = open_tab(client)
    page = tab.page
    expect(body_rows(page, "ports")).to_have_count(6)
    tab.allow(HTTP_ERROR)
    service.raise_next = ConfigError("porttool.toml has no [paths] table.")
    page.click("#btn-reload")
    message = page.locator("#messages .msg-error")
    expect(message).to_have_text(
        re.compile(r"Reload: \[PT-E100\] porttool.toml has no \[paths\] table\.")
    )
    expect(page.locator("#job-strip")).to_be_hidden()
    message.get_by_role("button", name="Dismiss").click()
    expect(page.locator("#messages .msg")).to_have_count(0)
    service.raise_next = RuntimeError("boom")
    page.click("#btn-reload")
    expect(page.locator("#messages .msg-error")).to_contain_text("[internal] Unexpected error.")
    expect(body_rows(page, "ports")).to_have_count(6)  # the page keeps working


def test_a_job_failure_is_shown_with_the_log_path(
    start_server: Starter, open_tab: TabOpener, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = Path("C:/Users/me/AppData/Local/PortTool/logs/porttool.log")
    monkeypatch.setattr("porttool.gui.server.current_log_file", lambda: log_file)
    service, client = start_server()
    page = open_tab(client).page
    expect(body_rows(page, "ports")).to_have_count(6)
    service.fail_next = "DVWspChecker reported 3 errors."
    page.click("#btn-reload")
    message = page.locator("#messages .msg-error")
    expect(message).to_contain_text("Loading the repo failed: DVWspChecker reported 3 errors.")
    expect(message).to_contain_text(f"Log file: {log_file}")
    expect(page.locator("#job-strip")).to_be_hidden()
    expect(page.locator("#btn-reload")).to_be_enabled()


def test_page_says_when_porttool_was_stopped(start_server: Starter, open_tab: TabOpener) -> None:
    _, client = start_server()
    tab = open_tab(client)
    page = tab.page
    expect(body_rows(page, "ports")).to_have_count(6)
    tab.allow(r"Failed to load resource: net::ERR_")
    tab.allow(HTTP_ERROR)
    client.handle.close()
    message = page.locator("#messages .msg-error")
    expect(message).to_contain_text("PortTool does not answer")
    expect(message).to_have_count(1)
    page.wait_for_timeout(2000)  # further failed polls do not add more messages
    expect(page.locator("#messages .msg")).to_have_count(1)
    expect(body_rows(page, "ports")).to_have_count(6)  # what was loaded stays readable


# --------------------------------------------------------------------------- requests tab


def test_export_from_the_page(start_server: Starter, open_tab: TabOpener, tmp_path: Path) -> None:
    service, client = start_server()
    page = open_tab(client).page
    open_requests(page)
    target = page.locator("#export-target")
    expect(target).to_have_value(re.compile(r"porttool_requests_\d{4}-\d{2}-\d{2}\.xlsx$"))
    wanted = tmp_path / "requests for teams.xlsx"
    target.fill(str(wanted))
    page.click("#btn-export")
    result = page.locator("#export-result")
    expect(result).to_contain_text("Workbook written.")
    expect(result).to_contain_text(str(wanted))
    assert service.export_targets == [wanted]
    time.sleep(1.7)  # a later state poll must not overwrite what the user typed
    expect(target).to_have_value(str(wanted))


def test_request_form_builds_a_request_and_validate_shows_results(
    start_server: Starter, open_tab: TabOpener
) -> None:
    service, client = start_server()
    page = open_tab(client).page
    open_requests(page)
    expect(page.locator("#btn-validate-form")).to_be_disabled()
    page.click("#btn-add-request")
    expect(page.locator("#form-error")).to_have_text("Fill in: SWC, Port, Direction, Interface.")
    assert page.locator("#dl-swcs option").count() == 4  # dropdown values from /api/lists
    assert page.locator("#f-direction option").all_inner_texts() == ["", "P", "R", "PR"]

    add_port_request(page, "SwcA", "PpNew")
    expect(page.locator("#form-list tbody tr")).to_have_count(1)
    expect(page.locator("#f-port")).to_have_value("")
    expect(page.locator("#f-requester")).to_have_value("Mona")  # kept for the next request
    add_port_request(page, "SwcB", "PpWrong")
    add_port_request(page, "SwcB", "PpGone")
    expect(page.locator("#form-count")).to_have_text("(3)")

    page.get_by_role("button", name="Edit request 2").click()
    expect(page.locator("#f-port")).to_have_value("PpWrong")
    expect(page.locator("#btn-add-request")).to_have_text("Save changes")
    page.fill("#f-port", "reject_me")
    page.fill("#f-comment", "second try")
    page.click("#btn-add-request")
    page.get_by_role("button", name="Remove request 3").click()
    expect(page.locator("#form-list tbody tr")).to_have_count(2)
    expect(page.locator("#form-list tbody tr").nth(1)).to_contain_text("SwcB \u00b7 reject_me")

    page.click("#btn-validate-form")
    rows = page.locator("#results-table tbody tr")
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator(".badge")).to_have_text("accepted")
    expect(rows.nth(1).locator(".badge")).to_have_text("rejected")
    expect(rows.nth(1)).to_contain_text("VAL-NAME-001")
    expect(rows.nth(1)).to_contain_text("does not follow the naming rule")
    expect(rows.nth(0)).to_contain_text("Request form \u00b7 Ports \u00b7 row 1")
    expect(page.locator("#plan-preview")).to_have_text("add port PpNew to /ComponentTypes/SwcA")
    expect(page.locator("#plan-files")).to_contain_text("SwcA.arxml")
    expect(page.locator("#validation-findings")).to_contain_text("VAL-INFO-001")

    first, second = service.dry_run_rows
    assert (first.sheet, first.action, first.requester) == ("Ports", "ADD", "Mona")
    assert first.values["swc"] == "SwcA" and first.values["port"] == "PpNew"
    assert first.values["direction"] == "P" and first.values["interface"] == "IfSpeed"
    assert second.values["port"] == "reject_me" and second.comment == "second try"

    page.get_by_role("button", name=re.compile(r"^rejected \(1\)")).click()
    expect(rows).to_have_count(1)
    expect(rows.first.locator(".badge")).to_have_text("rejected")
    page.get_by_role("button", name=re.compile(r"^All \(2\)")).click()
    expect(rows).to_have_count(2)
    expect(page.locator("#apply-blockers")).to_have_text(
        "1 accepted request is ready to be applied."
    )
    expect(page.locator("#tab-pending .tab-count")).to_have_text("0")


def test_request_form_suggests_ports_and_runnables_of_the_chosen_swc(
    start_server: Starter, open_tab: TabOpener
) -> None:
    service, client = start_server()
    page = open_tab(client).page
    open_requests(page)
    page.select_option("#form-kind", "runnable_access")
    page.fill("#f-swc", "SwcB")
    page.locator("#f-swc").blur()
    expect(page.locator("#dl-suggest-f-port option")).to_have_count(2)
    expect(page.locator("#dl-suggest-f-runnable option")).to_have_count(2)
    values = page.locator("#dl-suggest-f-runnable option").evaluate_all("o => o.map(x => x.value)")
    assert values == ["PpDiag_ReadDtc", "RunB_OnSpeed"]
    page.fill("#f-runnable", "RunB_OnSpeed")
    page.fill("#f-port", "RpSpeed")
    page.fill("#f-target", "Speed")
    page.select_option("#f-access_kind", "explicit read")
    page.click("#btn-add-request")
    page.select_option("#form-kind", "connection")
    page.fill("#f-provider_swc", "SwcM")
    page.fill("#f-provider_port", "PpTorque")
    page.fill("#f-requester_swc", "SwcA")
    page.fill("#f-requester_port", "RpTorque")
    page.click("#btn-add-request")
    page.click("#btn-validate-form")
    expect(page.locator("#results-table tbody tr")).to_have_count(2)
    access, connection = service.dry_run_rows
    assert access.sheet == "RunnableAccess" and access.values["access_kind"] == "explicit read"
    assert connection.sheet == "Connections" and connection.values["requester_port"] == "RpTorque"


def test_request_form_interface_with_elements(start_server: Starter, open_tab: TabOpener) -> None:
    service, client = start_server()
    page = open_tab(client).page
    open_requests(page)
    page.select_option("#form-kind", "interface")
    expect(page.locator("#form-children")).to_be_visible()
    page.fill("#f-name", "IfNew")
    page.select_option("#f-kind", "sender-receiver")
    first = page.locator("#children-rows .child-row").nth(0)
    first.get_by_label("Element or operation").fill("Speed")
    first.get_by_label("Data type").fill("uint16")
    page.click("#btn-add-child")
    second = page.locator("#children-rows .child-row").nth(1)
    second.get_by_label("Data type").fill("uint16")
    page.click("#btn-add-request")
    expect(page.locator("#form-error")).to_have_text(
        "Every element row needs: Element or operation."
    )
    second.get_by_label("Element or operation").fill("Valid")
    second.get_by_label("Queued (Y/N)").select_option("N")
    page.click("#btn-add-request")
    expect(page.locator("#form-list tbody tr")).to_contain_text("(+2 element rows)")
    page.click("#btn-validate-form")
    expect(page.locator("#results-table tbody tr")).to_have_count(3)
    sheets = [row.sheet for row in service.dry_run_rows]
    assert sheets == ["Interfaces", "InterfaceElements", "InterfaceElements"]
    interface, speed, valid = service.dry_run_rows
    assert interface.values["name"] == "IfNew" and interface.values["kind"] == "sender-receiver"
    assert speed.values["interface"] == "IfNew" and speed.values["element"] == "Speed"
    assert valid.values["element"] == "Valid" and valid.values["queued"] == "N"


def test_upload_of_a_real_xlsx_reaches_the_service(
    start_server: Starter, open_tab: TabOpener, tmp_path: Path
) -> None:
    service, client = start_server()
    page = open_tab(client).page
    open_requests(page)
    first = tmp_path / "Team Alpha.xlsx"
    second = tmp_path / "Team Beta.xlsx"
    first.write_bytes(xlsx_bytes("alpha"))
    second.write_bytes(xlsx_bytes("beta"))
    expect(page.locator("#btn-validate-files")).to_be_disabled()
    page.set_input_files("#file-input", [str(first), str(second)])
    expect(page.locator("#file-list li")).to_have_count(2)
    expect(page.locator("#file-list")).to_contain_text("Team Alpha.xlsx")
    page.get_by_role("button", name="Remove Team Beta.xlsx").click()
    expect(page.locator("#file-list li")).to_have_count(1)
    page.set_input_files("#file-input", [str(second)])
    page.click("#btn-validate-files")
    rows = page.locator("#results-table tbody tr")
    expect(rows).to_have_count(3)
    assert [data for _, data in service.dry_run_files] == [first.read_bytes(), second.read_bytes()]
    expect(rows.nth(0)).to_contain_text("Team Alpha.xlsx \u00b7 Ports \u00b7 row 12")
    expect(rows.nth(2).locator(".badge")).to_have_text("waiting for model owner")
    expect(page.locator("#status-filter .chip")).to_have_count(4)
    page.click("#tab-pending")
    expect(body_rows(page, "pending")).to_have_count(1)
    assert cells(page, "pending")[:2] == ["waiting for model owner", "add_port"]


def test_apply_confirmation_live_progress_and_result(
    start_server: Starter, open_tab: TabOpener
) -> None:
    service, client = start_server()
    page = open_tab(client).page
    open_requests(page)
    expect(page.locator("#btn-apply")).to_be_disabled()
    expect(page.locator("#apply-blockers")).to_have_text("Validate requests first.")
    validate_one_port(page)
    service.steps, service.step_s = 6, 0.25
    page.click("#btn-apply")
    dialog = page.locator("#confirm-dialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#confirm-title")).to_have_text("Apply 1 request?")
    expect(page.locator("#confirm-text")).to_contain_text("apply 1 accepted request")
    expect(page.locator("#confirm-text")).to_contain_text("new git branch will be created")
    page.click("#confirm-cancel")
    expect(dialog).to_be_hidden()
    assert "start_apply" not in service.calls
    expect(page.locator("#btn-apply")).to_be_enabled()

    page.click("#btn-apply")
    page.click("#confirm-ok")
    expect(page.locator("#job-strip")).to_be_visible()
    expect(page.locator("#job-name")).to_have_text("Applying requests")
    expect(page.locator("#btn-apply")).to_be_disabled()
    log = page.locator("#apply-log")
    expect(log).to_contain_text("apply started")
    expect(log).to_contain_text("step 2 of 6 finished")  # the log grows while the job runs
    expect(page.locator("#job-strip")).to_be_visible()
    result = page.locator("#apply-result")
    expect(result).to_contain_text("Apply finished.")
    expect(result).to_contain_text(service.report_file)
    expect(result).to_contain_text("porttool/apply-20261111-120000")
    expect(result).to_contain_text("1 applied")
    expect(log).to_contain_text("apply finished")
    expect(page.locator("#job-strip")).to_be_hidden()
    expect(page.locator("#btn-apply")).to_be_disabled()
    expect(page.locator("#apply-blockers")).to_contain_text("validate again")
    assert service.calls.count("start_apply") == 1


def test_apply_can_be_cancelled_and_a_failed_apply_shows_the_error(
    start_server: Starter, open_tab: TabOpener
) -> None:
    service, client = start_server()
    page = open_tab(client).page
    validate_one_port(page)
    service.hang_next = True
    page.click("#btn-apply")
    page.click("#confirm-ok")
    expect(page.locator("#apply-log")).to_contain_text("step 3 of 3 finished")
    page.click("#btn-cancel-job")
    expect(page.locator("#btn-cancel-job")).to_be_hidden()
    expect(page.locator("#apply-result")).to_have_text("Apply was cancelled.")
    expect(page.locator("#apply-log")).to_contain_text("cancelled by the user")

    page.click("#btn-validate-form")
    expect(page.locator("#btn-apply")).to_be_enabled()
    service.fail_next = "git could not create the branch."
    page.click("#btn-apply")
    page.click("#confirm-ok")
    expect(page.locator("#apply-result")).to_contain_text(
        "Apply failed: git could not create the branch."
    )
    expect(page.locator("#messages .msg-error")).to_contain_text("Applying requests failed")


def test_apply_is_disabled_while_the_model_is_stale(
    start_server: Starter, open_tab: TabOpener
) -> None:
    service, client = start_server()
    page = open_tab(client).page
    validate_one_port(page)
    service.stale = True
    expect(page.locator("#btn-apply")).to_be_disabled()
    expect(page.locator("#apply-blockers")).to_contain_text("Files changed on disk")
    service.stale = False
    service.recovery = True
    expect(page.locator("#apply-blockers")).to_contain_text("roll back first")
    expect(page.locator("#btn-apply")).to_be_disabled()
    expect(page.locator("#btn-validate-form")).to_be_disabled()


def test_page_works_at_200_percent_zoom(start_server: Starter, open_tab: TabOpener) -> None:
    """A 1280 x 800 window at 200 % zoom has a 640 x 400 CSS pixel viewport."""
    _, client = start_server()
    page = open_tab(client, width=640, height=400).page
    expect(body_rows(page, "ports").first).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for selector in ("#btn-reload", "#tab-requests", "#search-ports"):
        box = page.locator(selector).bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 640, selector
    page.click("#tab-requests")
    expect(page.locator("#btn-export")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement !== document.body")


def test_tabs_are_usable_with_the_keyboard(start_server: Starter, open_tab: TabOpener) -> None:
    _, client = start_server()
    page = open_tab(client).page
    expect(body_rows(page, "ports")).to_have_count(6)
    page.locator("#tab-ports").focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#tab-runnable_access")).to_be_focused()
    expect(page.locator("#panel-runnable_access")).to_be_visible()
    page.keyboard.press("End")
    expect(page.locator("#tab-requests")).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Home")
    expect(page.locator("#panel-components")).to_be_visible()
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    assert outline != "none", "the focused tab must show a focus ring"


# --------------------------------------------------------------------------- screenshots


def test_screenshots_for_the_handoff(
    start_server: Starter, open_tab: TabOpener, large_model: Model
) -> None:
    """Writes the two screenshots of porttool/gui/docs when PORTTOOL_GUI_SCREENSHOTS is set."""
    folder = os.environ.get("PORTTOOL_GUI_SCREENSHOTS")
    if not folder:
        pytest.skip("set PORTTOOL_GUI_SCREENSHOTS=<folder> to write the screenshots")
    _, client = start_server(model=large_model)
    client.wait_tables()
    page = open_tab(client, width=1440, height=900).page
    expect(count_text(page, "ports")).to_have_text("20,000 of 20,000 rows")
    column_filter(page, "ports", "Interface").fill("if_00")
    expect(count_text(page, "ports")).to_have_text(re.compile(r"of 20,000 rows"))
    page.locator("#panel-ports").get_by_role("columnheader", name="Port", exact=True).click()
    expect(body_rows(page, "ports").first).to_be_visible()
    page.screenshot(path=str(Path(folder) / "ports_table.png"))
    open_requests(page)
    add_port_request(page, "Swc_00001", "PpNewSpeed")
    add_port_request(page, "Swc_00002", "reject_this_name")
    page.select_option("#form-kind", "port")
    page.fill("#f-swc", "Swc_00003")
    page.fill("#f-port", "PpModelOwned")
    page.select_option("#f-direction", "P")
    page.fill("#f-interface", "If_0001")
    page.fill("#f-comment", "model owner decides")
    page.click("#btn-add-request")
    page.click("#btn-validate-form")
    expect(page.locator("#results-table tbody tr")).to_have_count(3)
    page.locator("#h-results").scroll_into_view_if_needed()
    page.screenshot(path=str(Path(folder) / "dry_run_results.png"))
