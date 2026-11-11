"""Tests for porttool.core.log: rotating file log, console handler, exception hooks."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from porttool.core import log
from porttool.core.errors import PortToolError


@pytest.fixture(autouse=True)
def _clean_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # monkeypatch restores the hooks that install_exception_hooks() replaces.
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    yield
    log.shutdown_logging()


def _flush() -> None:
    for handler in logging.getLogger("porttool").handlers:
        handler.flush()


def test_setup_logging_writes_to_the_injected_folder(tmp_path: Path) -> None:
    log_file = log.setup_logging(tmp_path / "logs with space ä")
    assert log_file == tmp_path / "logs with space ä" / "porttool.log"
    assert log.current_log_file() == log_file
    logging.getLogger("porttool.reader").info("loaded %s", "Swc_Ä.arxml")
    logging.getLogger("porttool.reader").debug("not written at INFO level")
    _flush()
    text = log_file.read_text(encoding="utf-8")
    assert "INFO" in text and "porttool.reader: loaded Swc_Ä.arxml" in text
    assert "not written" not in text


def test_default_log_dir_is_below_localappdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert log.default_log_dir() == tmp_path / "PortTool" / "logs"
    assert log.setup_logging() == tmp_path / "PortTool" / "logs" / "porttool.log"


def test_log_file_rotates(tmp_path: Path) -> None:
    log_file = log.setup_logging(tmp_path, max_bytes=2000, backup_count=2)
    logger = logging.getLogger("porttool.test")
    for index in range(200):
        logger.info("line %04d %s", index, "x" * 50)
    _flush()
    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == ["porttool.log", "porttool.log.1", "porttool.log.2"]
    assert log_file.stat().st_size <= 2000
    assert "line 0199" in log_file.read_text(encoding="utf-8")


def test_setup_twice_replaces_the_handlers(tmp_path: Path) -> None:
    first = log.setup_logging(tmp_path / "one")
    second = log.setup_logging(tmp_path / "two")
    owned = [h for h in logging.getLogger("porttool").handlers if getattr(h, "_porttool_owned", 0)]
    assert len(owned) == 1
    logging.getLogger("porttool.x").warning("only in two")
    _flush()
    assert "only in two" in second.read_text(encoding="utf-8")
    assert "only in two" not in first.read_text(encoding="utf-8")
    log.shutdown_logging()
    assert log.current_log_file() is None
    assert not [
        h for h in logging.getLogger("porttool").handlers if getattr(h, "_porttool_owned", 0)
    ]


def test_console_handler_for_cli_use(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log_file = log.setup_logging(tmp_path, console=True)
    logger = logging.getLogger("porttool.cli")
    logger.info("quiet on the console")
    logger.warning("shown on the console")
    _flush()
    err = capsys.readouterr().err
    assert "WARNING: shown on the console" in err
    assert "quiet on the console" not in err
    assert "quiet on the console" in log_file.read_text(encoding="utf-8")


def test_verbose_console_shows_debug(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log.setup_logging(tmp_path, console=True, console_level=logging.DEBUG)
    logging.getLogger("porttool.cli").debug("detail")
    assert "DEBUG: detail" in capsys.readouterr().err


def test_log_folder_cannot_be_created(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(PortToolError, match="cannot be created"):
        log.setup_logging(blocker / "logs")
    assert log.current_log_file() is None


def test_sys_excepthook_logs_the_traceback_and_tells_the_user(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_file = log.setup_logging(tmp_path)
    log.install_exception_hooks()
    try:
        raise ValueError("boom in main")
    except ValueError as error:
        sys.excepthook(type(error), error, error.__traceback__)
    _flush()
    text = log_file.read_text(encoding="utf-8")
    assert "CRITICAL" in text and "Unhandled exception" in text
    assert "Traceback (most recent call last)" in text
    assert "ValueError: boom in main" in text
    err = capsys.readouterr().err
    assert "PortTool: unexpected error (ValueError: boom in main)." in err
    assert str(log_file) in err


def test_sys_excepthook_leaves_ctrl_c_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = log.setup_logging(tmp_path)
    log.install_exception_hooks()
    seen: list[type[BaseException]] = []
    monkeypatch.setattr(sys, "__excepthook__", lambda kind, exc, tb: seen.append(kind))
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    _flush()
    assert seen == [KeyboardInterrupt]
    assert log_file.read_text(encoding="utf-8") == ""


def test_thread_exception_is_logged_and_does_not_end_the_program(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_file = log.setup_logging(tmp_path)
    log.install_exception_hooks()

    def work() -> None:
        raise RuntimeError("boom in thread")

    thread = threading.Thread(target=work, name="worker-7")
    thread.start()
    thread.join()
    _flush()
    text = log_file.read_text(encoding="utf-8")
    assert "Unhandled exception in thread worker-7" in text
    assert "RuntimeError: boom in thread" in text and "Traceback" in text
    assert "unexpected error in thread worker-7" in capsys.readouterr().err


def test_thread_system_exit_is_not_an_error(tmp_path: Path) -> None:
    log_file = log.setup_logging(tmp_path)
    log.install_exception_hooks()
    thread = threading.Thread(target=sys.exit)
    thread.start()
    thread.join()
    _flush()
    assert log_file.read_text(encoding="utf-8") == ""


def test_asyncio_loop_exception_is_logged_and_the_loop_keeps_running(tmp_path: Path) -> None:
    log_file = log.setup_logging(tmp_path)
    loop = asyncio.new_event_loop()
    try:
        log.install_exception_hooks(loop)

        def bad_callback() -> None:
            raise KeyError("boom in callback")

        async def main() -> str:
            loop.call_soon(bad_callback)
            await asyncio.sleep(0.01)
            return "still running"

        assert loop.run_until_complete(main()) == "still running"
    finally:
        loop.close()
    _flush()
    text = log_file.read_text(encoding="utf-8")
    assert "asyncio:" in text and "KeyError: 'boom in callback'" in text and "Traceback" in text


def test_loop_exception_handler_without_exception_object(tmp_path: Path) -> None:
    log_file = log.setup_logging(tmp_path)
    loop = asyncio.new_event_loop()
    try:
        log.loop_exception_handler(loop, {"message": "Task was destroyed but it is pending!"})
    finally:
        loop.close()
    _flush()
    assert "asyncio: Task was destroyed" in log_file.read_text(encoding="utf-8")


def test_hooks_work_without_a_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = log.setup_logging(tmp_path)
    log.install_exception_hooks()
    monkeypatch.setattr(sys, "stderr", None)  # pythonw.exe has no stderr
    sys.excepthook(ValueError, ValueError("no console"), None)
    _flush()
    assert "ValueError: no console" in log_file.read_text(encoding="utf-8")
