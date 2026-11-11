r"""Logging: rotating file log, console handler, hooks for unhandled exceptions.

The log file is ``%LOCALAPPDATA%\PortTool\logs\porttool.log`` (5 MB, five backups).
Modules log through ``logging.getLogger(__name__)``; every such logger is a child of
``porttool``. Log names and paths, never file contents.

Call :func:`setup_logging` and :func:`install_exception_hooks` once at program start
(the CLI does). After that no exception can end a thread or the process silently:
the traceback is in the log and a one-line pointer to the log file is on stderr.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import sys
import threading
from pathlib import Path
from types import TracebackType
from typing import Any

from porttool.core.errors import PortToolError
from porttool.core.fsutil import local_app_dir

ROOT_LOGGER = "porttool"
LOG_FILE_NAME = "porttool.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

_FILE_FORMAT = "%(asctime)s %(levelname)-8s pid=%(process)d %(threadName)s %(name)s: %(message)s"
_CONSOLE_FORMAT = "%(levelname)s: %(message)s"
_OWNED = "_porttool_owned"

_LOG = logging.getLogger(__name__)
_log_file: Path | None = None


def default_log_dir() -> Path:
    r"""Return ``%LOCALAPPDATA%\PortTool\logs``."""
    return local_app_dir() / "logs"


def current_log_file() -> Path | None:
    """The log file set up by :func:`setup_logging`, or None before it ran."""
    return _log_file


def setup_logging(
    log_dir: Path | None = None,
    *,
    console: bool = False,
    level: int = logging.INFO,
    console_level: int = logging.WARNING,
    max_bytes: int = MAX_BYTES,
    backup_count: int = BACKUP_COUNT,
) -> Path:
    """Attach the rotating file handler (and, for the CLI, a console handler on stderr).

    Returns the log file path. Calling it again replaces the handlers of the previous
    call. Raises ``PortToolError`` when the log folder or file cannot be created.
    """
    global _log_file
    folder = log_dir if log_dir is not None else default_log_dir()
    log_file = folder / LOG_FILE_NAME
    try:
        folder.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
    except OSError as error:
        raise PortToolError(
            f"The log file {log_file} cannot be created: {error.strerror or error}",
            detail=repr(error),
        ) from error
    shutdown_logging()
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    handlers: list[logging.Handler] = [file_handler]
    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(console_level)
        console_handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
        handlers.append(console_handler)
    logger = logging.getLogger(ROOT_LOGGER)
    for handler in handlers:
        setattr(handler, _OWNED, True)
        logger.addHandler(handler)
    logger.setLevel(min(level, console_level) if console else level)
    _log_file = log_file
    return log_file


def shutdown_logging() -> None:
    """Remove and close the handlers that :func:`setup_logging` attached."""
    global _log_file
    logger = logging.getLogger(ROOT_LOGGER)
    for handler in list(logger.handlers):
        if getattr(handler, _OWNED, False):
            logger.removeHandler(handler)
            handler.close()
    _log_file = None


# --------------------------------------------------------------------------- exception hooks


def _tell_user(summary: str) -> None:
    """One line on stderr that says what happened and where the details are."""
    stream = sys.stderr
    if stream is None:  # no console (pythonw): the log file is the only place
        return
    where = f" Details: {_log_file}" if _log_file is not None else ""
    try:
        print(f"PortTool: {summary}.{where}", file=stream, flush=True)
    except (OSError, ValueError) as error:  # stderr closed or broken
        _LOG.debug("stderr is not writable: %r", error)


def _excepthook(
    exc_type: type[BaseException], exc: BaseException, traceback: TracebackType | None
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, traceback)
        return
    _LOG.critical("Unhandled exception", exc_info=(exc_type, exc, traceback))
    _tell_user(f"unexpected error ({exc_type.__name__}: {exc})")


def _threading_excepthook(args: threading.ExceptHookArgs) -> None:
    if issubclass(args.exc_type, SystemExit):
        return
    name = args.thread.name if args.thread is not None else "?"
    error = args.exc_value
    if error is None:  # allowed by the threading API, practically never the case
        _LOG.critical("Unhandled %s in thread %s", args.exc_type.__name__, name)
    else:
        _LOG.critical(
            "Unhandled exception in thread %s",
            name,
            exc_info=(args.exc_type, error, args.exc_traceback),
        )
    _tell_user(f"unexpected error in thread {name} ({args.exc_type.__name__}: {error})")


def loop_exception_handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    """asyncio exception handler: log what asyncio reports, keep the loop running."""
    message = context.get("message", "Unhandled exception in the event loop")
    source = ", ".join(
        f"{key}={context[key]!r}" for key in ("task", "future", "handle") if key in context
    )
    error = context.get("exception")
    if isinstance(error, BaseException):
        _LOG.error(
            "asyncio: %s (%s)",
            message,
            source,
            exc_info=(type(error), error, error.__traceback__),
        )
    else:
        _LOG.error("asyncio: %s (%s)", message, source)


def install_exception_hooks(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Route unhandled exceptions to the log: ``sys.excepthook``, ``threading.excepthook``
    and, if ``loop`` is given, that asyncio loop's exception handler.

    The engine installs :func:`loop_exception_handler` on its own loop by itself.
    Ctrl+C in the main thread keeps its normal behaviour.
    """
    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook
    if loop is not None:
        loop.set_exception_handler(loop_exception_handler)
