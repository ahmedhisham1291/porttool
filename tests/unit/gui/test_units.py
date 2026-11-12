"""Small pure functions of the GUI package: settings, upload helpers, query parsing."""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Callable

import pytest

from porttool.gui.errors import ApiError, GuiError
from porttool.gui.server import (
    UploadedFile,
    check_xlsx,
    display_name,
    open_in_browser,
    parse_multipart,
    safe_upload_name,
    serve,
)
from porttool.gui.settings import GuiSettings, settings_from, settings_of
from porttool.gui.tables import TABLE_DEF_BY_NAME, Query, QueryError, TableData, parse_query
from tests.unit.gui.conftest import multipart, xlsx_bytes
from tests.unit.gui.fake_service import FakeService


def test_settings_defaults_and_values() -> None:
    assert settings_of(None) == GuiSettings()
    assert GuiSettings().max_upload_bytes == 50 * 1024 * 1024
    table = {"port": 8765, "max_upload_mb": 5, "stale_check_interval_s": 1, "partner_limit": 0}
    settings = settings_from(table)
    assert (settings.port, settings.max_upload_mb, settings.partner_limit) == (8765, 5, 0)
    assert settings.stale_check_interval_s == 1.0 and settings.max_upload_files == 20


def test_unusable_settings_are_logged_and_ignored(caplog: pytest.LogCaptureFixture) -> None:
    table: dict[str, object] = {
        "port": 70000,
        "max_upload_mb": "big",
        "max_upload_files": 0,
        "stale_check_interval_s": True,
        "partner_limit": 2.5,
        "colour": "blue",
    }
    with caplog.at_level(logging.WARNING, logger="porttool.gui.settings"):
        assert settings_from(table) == GuiSettings()
    assert len(caplog.records) == 6
    assert "colour" in caplog.text and "out of range" in caplog.text


def test_settings_come_from_the_gui_table_of_the_config() -> None:
    service = FakeService(gui={"max_upload_mb": 3})
    assert settings_of(service.config()).max_upload_mb == 3


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("Team A requests.xlsx", "01_Team_A_requests.xlsx"),
        ("..\\..\\Windows\\evil.XLSX", "01_evil.xlsx"),
        ("/etc/passwd", "01_passwd.xlsx"),
        ("طلبات.xlsx", "01_workbook.xlsx"),
        ("CON.xlsx", "01_CON.xlsx"),
        ("a" * 300 + ".xlsx", "01_" + "a" * 60 + ".xlsx"),
        ("", "01_workbook.xlsx"),
    ],
)
def test_saved_upload_names_are_generated_and_safe(original: str, expected: str) -> None:
    assert safe_upload_name(original, 1) == expected


def test_display_name_keeps_only_the_last_path_part() -> None:
    assert display_name("C:\\Users\\me\\Team.xlsx") == "Team.xlsx"
    assert display_name("a/b/طلبات.xlsx") == "طلبات.xlsx"
    assert display_name("bad\x00name\n.xlsx") == "badname.xlsx"
    assert display_name("") == "workbook.xlsx"


def test_multipart_keeps_binary_content_exactly() -> None:
    tricky = b"\r\n--not-the-boundary\r\n\x00\xff--\r\n"
    content_type, body = multipart([("a.xlsx", tricky), ("b.xlsx", b"")])
    files = parse_multipart(content_type, body)
    assert files == [UploadedFile("a.xlsx", tricky), UploadedFile("b.xlsx", b"")]
    quoted = content_type.replace("boundary=", 'boundary="') + '"'
    assert parse_multipart(quoted, body) == files


def test_multipart_ignores_plain_fields_and_rejects_garbage() -> None:
    body = (
        b'--B\r\nContent-Disposition: form-data; name="note"\r\n\r\nhello\r\n'
        b'--B\r\nContent-Disposition: form-data; name="file"; filename="x.xlsx"\r\n\r\nDATA\r\n'
        b"--B--\r\n"
    )
    assert parse_multipart("multipart/form-data; boundary=B", body) == [
        UploadedFile("x.xlsx", b"DATA")
    ]
    with pytest.raises(ApiError, match="boundary"):
        parse_multipart("multipart/form-data", body)
    with pytest.raises(ApiError, match="not a valid multipart"):
        parse_multipart("multipart/form-data; boundary=B", b"--B\r\nno header end")


def test_check_xlsx_accepts_a_real_workbook_only() -> None:
    check_xlsx(UploadedFile("ok.XLSX", xlsx_bytes()))
    other_zip = io.BytesIO()
    with zipfile.ZipFile(other_zip, "w") as archive:
        archive.writestr("readme.txt", "not a workbook")
    for upload in (
        UploadedFile("notes.txt", xlsx_bytes()),
        UploadedFile("text.xlsx", b"just text"),
        UploadedFile("empty.xlsx", b""),
        UploadedFile("zip.xlsx", other_zip.getvalue()),
        UploadedFile("cut.xlsx", xlsx_bytes()[:200]),
    ):
        with pytest.raises(ApiError) as caught:
            check_xlsx(upload)
        assert (caught.value.status, caught.value.code) == (400, "not-xlsx")


def test_parse_query_normalises_and_checks() -> None:
    ports = TABLE_DEF_BY_NAME["ports"]
    query = parse_query(
        ports,
        {"offset": ["5"], "limit": ["900"], "sort": ["port"], "dir": ["DESC"], "q": ["  AbC\t "]}
        | {"f.swc": [" Swc "], "f.port": ["  "], "only": ["unconnected"], "other": ["x"]},
    )
    assert query == Query(5, 500, "port", True, "abc", (("swc", "swc"),), "unconnected")
    assert parse_query(ports, {}) == Query()
    with pytest.raises(QueryError, match="no column"):
        parse_query(ports, {"sort": ["nope"]})


def test_numeric_columns_sort_by_number_with_text_last() -> None:
    triggers = TABLE_DEF_BY_NAME["triggers"]
    index = triggers.column_index("period_ms")
    rows = []
    for period in ("100", "", "5", "20.5", "n/a", "1e3"):
        row = [""] * len(triggers.columns)
        row[index] = period
        rows.append(row)
    data = TableData(triggers, rows)
    ordered = [r[index] for r in data.query(Query(sort="period_ms")).rows]
    assert ordered == ["5", "20.5", "100", "1e3", "", "n/a"]
    reverse = [r[index] for r in data.query(Query(sort="period_ms", descending=True)).rows]
    assert reverse == ["n/a", "", "1e3", "100", "20.5", "5"]


def test_serve_refuses_other_hosts_and_bad_ports() -> None:
    service = FakeService(loaded=False)
    with pytest.raises(GuiError, match="this computer only"):
        serve(service, host="0.0.0.0", open_browser=False)
    with pytest.raises(GuiError, match="not a valid port"):
        serve(service, port=99999, open_browser=False)


def _recorder(opened: list[str]) -> Callable[[str], bool]:
    def record(url: str) -> bool:
        opened.append(url)
        return True

    return record


def test_open_in_browser_reports_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: False)
    assert open_in_browser("http://127.0.0.1:1/") is False

    def broken(url: str) -> bool:
        raise webbrowser.Error("no browser")

    monkeypatch.setattr(webbrowser, "open", broken)
    assert open_in_browser("http://127.0.0.1:1/") is False
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", _recorder(opened))
    assert open_in_browser("http://127.0.0.1:1/?token=t") is True and len(opened) == 1


def test_serve_opens_the_browser_with_the_token_address(monkeypatch: pytest.MonkeyPatch) -> None:
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", _recorder(opened))
    with serve(FakeService(loaded=False), token="fixed-token") as handle:
        assert opened == [handle.url] and handle.url.endswith("/?token=fixed-token")
        assert handle.token == "fixed-token"
    assert handle.closed
