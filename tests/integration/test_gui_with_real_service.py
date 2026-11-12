"""The web interface on top of the REAL service (the GUI agent only had a fake one)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import expect

from porttool.gui import serve
from porttool.pipeline import build_service
from tests.integration.test_end_to_end_real_parts import _requests
from tests.unit.gui.conftest import (  # noqa: F401 - pytest fixtures
    Client,
    TabOpener,
    browser,
    multipart,
    open_tab,
)
from tests.unit.pipeline.support import edited_workbook, make_repo


def _job(client: Client, path: str, payload: object | None = None) -> dict[str, Any]:
    return client.wait_job(client.start(path, payload), timeout=120)


def test_gui_api_full_cycle_on_the_real_service(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", tmp_path / "home")
    service = build_service(
        journal_dir=tmp_path / "journals", lock_dir=tmp_path / "locks", git_env=repo.env
    )
    service.open_repo(repo.root)
    handle = serve(service, open_browser=False)
    client = Client(handle)
    try:
        assert _job(client, "/api/load")["state"] == "done"
        state = client.ok("/api/state")
        assert state["model"]["counts"]["ports"] == len(repo.truth.ports)
        client.wait_tables()
        ports = client.ok("/api/tables/ports?limit=500")
        assert ports["total"] == len(repo.truth.ports)

        requests, names = _requests(repo)
        workbook = edited_workbook(service, tmp_path, requests)
        content_type, body = multipart([("team_requests.xlsx", workbook.read_bytes())])
        reply = client.post(
            "/api/dry-run/upload", body=body, headers={"Content-Type": content_type}
        )
        assert reply.status == 200, reply.body
        dry = client.wait_job(str(reply.json()["job_id"]), timeout=120)
        assert dry["state"] == "done", dry
        validation = client.ok("/api/validation")
        assert validation["counts"] == {"accepted": len(requests)}, validation

        applied = _job(client, "/api/apply")
        assert applied["state"] == "done", applied
        report = client.ok("/api/report")
        assert report["ok"] is True, report
        assert repo.branch().startswith("porttool/")

        client.wait_tables()
        found = client.ok("/api/tables/ports?q=PpE2E&limit=50")
        assert found["filtered"] == 1, found
        connections = client.ok("/api/tables/connections?q=RpE2E&limit=50")
        assert connections["filtered"] == 1, connections
        assert names["iface"]
    finally:
        handle.close()
        service.close()


def test_gui_page_shows_the_real_repo_without_console_errors(
    tmp_path: Path, open_tab: TabOpener
) -> None:
    repo = make_repo(tmp_path / "repo", tmp_path / "home")
    service = build_service(
        journal_dir=tmp_path / "journals", lock_dir=tmp_path / "locks", git_env=repo.env
    )
    service.open_repo(repo.root)
    handle = serve(service, open_browser=False)
    try:
        tab = open_tab(Client(handle))
        page = tab.page
        # The page loads the repo by itself, then shows the counts and the tables.
        expect(page.locator("#model-counts")).to_contain_text(
            f"{len(repo.truth.ports)} ports", timeout=60000
        )
        page.click("#tab-ports")
        first_port = repo.truth.ports[0].name
        expect(page.locator("#panel-ports .vt-body")).to_contain_text(first_port, timeout=30000)
        page.click("#tab-unconnected")
        expect(page.locator("#tab-unconnected .tab-count")).to_have_text(
            str(len(repo.truth.unconnected)), timeout=30000
        )
        page.click("#tab-requests")
        expect(page.locator("#h-export")).to_be_visible()
        assert tab.unexpected() == []
        page.screenshot(path=str(tmp_path / "real_service.png"), full_page=True)
    finally:
        handle.close()
        service.close()
