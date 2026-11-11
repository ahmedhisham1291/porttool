"""The local web server behind ``porttool gui``.

Standard library only. The server listens on 127.0.0.1, checks the Host header and a
session token on every API call, serves the static page and answers a small JSON API.
Handlers never do long work: they read from the ``AppService`` or start one of its jobs.
The rows of the tables are built once per model in a worker thread (``TableCache``).
"""

from __future__ import annotations

import datetime
import io
import json
import logging
import re
import secrets
import shutil
import socket
import socketserver
import tempfile
import threading
import time
import webbrowser
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import parse_qs, unquote, urlsplit

import porttool
from porttool.core.config import ProjectConfig
from porttool.core.errors import (
    ConfigError,
    JobCancelled,
    PortToolError,
    RepoStateError,
    WorkbookError,
)
from porttool.core.interfaces import AppService, JobView, ValidationOutcome
from porttool.core.log import current_log_file
from porttool.core.models import Model
from porttool.core.pending import PendingQueue
from porttool.core.results import RequestStatus
from porttool.gui.errors import ApiError, GuiError
from porttool.gui.forms import form_schema, rows_from_json
from porttool.gui.settings import GuiSettings, settings_of
from porttool.gui.tables import (
    TABLE_DEF_BY_NAME,
    TABLE_DEFS,
    QueryError,
    TableData,
    build_labels,
    build_lists,
    build_pending_table,
    build_tables,
    parse_query,
    swc_details,
)
from porttool.gui.views import (
    ACTIVE_STATES,
    finding_json,
    job_json,
    report_json,
    validation_json,
)

_logger = logging.getLogger(__name__)

TOKEN_HEADER = "X-PortTool-Token"
_JSON_BODY_LIMIT = 8 * 1024 * 1024
_DRAIN_LIMIT = 1024 * 1024 * 1024  # an oversized body up to this size is read and dropped
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_NAME = re.compile(r"[A-Za-z0-9_-]+\.(?:html|css|js)")
#: Fixed types: the Windows registry can map .js to text/plain, which the browser refuses.
_CONTENT_TYPES: Mapping[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_XLSX_MAGIC = b"PK\x03\x04"


# --------------------------------------------------------------------------- uploads


@dataclass(frozen=True, slots=True)
class UploadedFile:
    name: str  # as the browser sent it; shown to the user, never used as a path
    data: bytes


def parse_multipart(content_type: str, body: bytes) -> list[UploadedFile]:
    """The files of a ``multipart/form-data`` body. Fields without a file name are ignored."""
    match = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type, re.IGNORECASE)
    if match is None:
        raise ApiError(400, "bad-upload", "The upload has no multipart boundary.")
    delimiter = b"\r\n--" + (match.group(1) or match.group(2)).encode("ascii", "replace")
    files: list[UploadedFile] = []
    for part in (b"\r\n" + body).split(delimiter)[1:]:
        if part.startswith(b"--"):
            break  # closing delimiter
        head, separator, data = part.partition(b"\r\n\r\n")
        if not separator:
            raise ApiError(400, "bad-upload", "The upload is not a valid multipart body.")
        headers = head.decode("utf-8", "replace")
        name = re.search(r'filename="((?:[^"\\]|\\.)*)"', headers, re.IGNORECASE)
        if name is None:
            continue
        files.append(UploadedFile(name=unquote(name.group(1)), data=data))
    return files


def display_name(original: str) -> str:
    """The last path part of a browser file name, without control characters."""
    name = PurePosixPath(original.replace("\\", "/")).name
    return "".join(ch for ch in name if ch >= " ")[:200] or "workbook.xlsx"


def safe_upload_name(original: str, number: int) -> str:
    """A generated file name for a saved upload: ``NN_<letters, digits, _ and ->.xlsx``."""
    stem = display_name(original)
    if stem.lower().endswith(".xlsx"):
        stem = stem[:-5]
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:60]
    return f"{number:02d}_{cleaned or 'workbook'}.xlsx"


def check_xlsx(upload: UploadedFile) -> None:
    """Raise a 400 unless the upload looks like an .xlsx workbook (name and content)."""
    shown = display_name(upload.name)
    if not shown.lower().endswith(".xlsx"):
        raise ApiError(400, "not-xlsx", f"'{shown}' is not an .xlsx workbook.")
    problem = f"'{shown}' is not a valid .xlsx workbook. Save it as .xlsx in Excel."
    if not upload.data.startswith(_XLSX_MAGIC):
        raise ApiError(400, "not-xlsx", problem)
    try:
        with zipfile.ZipFile(io.BytesIO(upload.data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ApiError(400, "not-xlsx", problem) from None
    if "xl/workbook.xml" not in names:
        raise ApiError(400, "not-xlsx", problem)


# --------------------------------------------------------------------------- table cache


@dataclass(frozen=True, slots=True)
class ModelTables:
    """Everything the page shows of one model, ready to be filtered and paged."""

    model_hash: str
    tables: Mapping[str, TableData]
    lists: Mapping[str, list[str]]
    swc_paths: Mapping[str, str]  # label shown in the page -> SWC path
    prototype_types: Mapping[str, tuple[str, ...]]  # prototype name -> SWC type paths
    pending_file: PendingQueue | None
    pending_note: str
    build_s: float


def build_model_tables(
    model: Model, config: ProjectConfig | None, partner_limit: int
) -> ModelTables:
    """Loop over the whole model once. Slow for a big model: runs in a worker thread."""
    started = time.perf_counter()
    labels = build_labels(model)
    prototype_types: dict[str, list[str]] = {}
    for prototype in model.prototypes.values():
        if prototype.type_path is not None:
            prototype_types.setdefault(prototype.name, []).append(prototype.type_path)
    pending: PendingQueue | None = None
    note = ""
    if config is not None:
        pending_path = config.repo_root / config.pending_file
        try:
            if pending_path.is_file():
                pending = PendingQueue.from_json(pending_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            _logger.warning("the pending queue %s cannot be read: %s", pending_path, error)
            note = f"The pending queue file could not be read: {pending_path}"
    return ModelTables(
        model_hash=model.model_hash,
        tables=build_tables(model, config, partner_limit),
        lists=build_lists(model),
        swc_paths={label: path for path, label in labels.components.items()},
        prototype_types={name: tuple(paths) for name, paths in prototype_types.items()},
        pending_file=pending,
        pending_note=note,
        build_s=time.perf_counter() - started,
    )


class TableCache:
    """The tables of the current model, keyed by ``model.model_hash``.

    ``get`` never blocks: it returns the tables when they are ready, otherwise it makes
    sure one worker thread is building them and returns ``None`` ("preparing").
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entry: ModelTables | None = None
        self._building: str | None = None
        self._failed: tuple[str, str] | None = None  # (model hash, message)

    def get(
        self, model: Model, config: ProjectConfig | None, partner_limit: int
    ) -> ModelTables | None:
        wanted = model.model_hash
        with self._lock:
            entry = self._entry
            if entry is not None and entry.model_hash == wanted:
                return entry
            if self._failed is not None and self._failed[0] == wanted:
                raise ApiError(500, "tables-failed", self._failed[1])
            if self._building != wanted:
                self._building = wanted
                threading.Thread(
                    target=self._build,
                    args=(model, config, partner_limit),
                    name="porttool-gui-tables",
                    daemon=True,
                ).start()
        return None

    def _build(self, model: Model, config: ProjectConfig | None, partner_limit: int) -> None:
        wanted = model.model_hash
        try:
            tables = build_model_tables(model, config, partner_limit)
        except Exception:  # boundary: thread worker
            _logger.exception("the tables of the model could not be prepared")
            with self._lock:
                if self._building == wanted:
                    self._building = None
                    self._failed = (wanted, f"The tables could not be prepared. {_log_hint()}")
            return
        _logger.info("tables of model %s prepared in %.2f s", wanted[:12], tables.build_s)
        with self._lock:
            if self._building == wanted:  # otherwise a newer model replaced this one meanwhile
                self._entry = tables
                self._building = None
                self._failed = None


def _log_hint() -> str:
    log_file = current_log_file()
    if log_file is None:
        return "No log file is being written."
    return f"The details are in the log file: {log_file}"


# --------------------------------------------------------------------------- application


@dataclass(frozen=True, slots=True)
class _Flags:
    model_hash: str | None
    stale: bool
    recovery: bool


def _bad_request(message: str) -> ApiError:
    return ApiError(400, "bad-request", message)


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _bad_request(f"'{key}' must be text.")
    return value.strip()


class GuiApp:
    """Everything behind the HTTP handler. Thread-safe."""

    def __init__(
        self, service: AppService, token: str, tmp_dir: Path, settings: GuiSettings | None = None
    ) -> None:
        self._service = service
        self._token = token
        self._tmp = tmp_dir
        self._fixed_settings = settings
        self._settings_for: tuple[ProjectConfig | None, GuiSettings] | None = None
        self._cache = TableCache()
        self._start_lock = threading.Lock()
        self._lock = threading.Lock()  # guards the small caches below
        self._job_times: dict[str, tuple[float, float | None]] = {}
        self._upload_names: dict[str, str] = {}
        self._upload_dir: Path | None = None
        self._pending_table: tuple[PendingQueue | None, TableData] | None = None
        self._accepted: tuple[ValidationOutcome, int] | None = None
        self._applied: ValidationOutcome | None = None  # the dry run an apply was started for
        self._flags = _Flags(None, False, False)
        self._flag_error = ""
        self._wake = threading.Event()
        self._closed = threading.Event()

    # ---- settings and background state

    def settings(self) -> GuiSettings:
        if self._fixed_settings is not None:
            return self._fixed_settings
        config = self._service.config()
        with self._lock:
            known = self._settings_for
            if known is None or known[0] is not config:
                known = (config, settings_of(config))
                self._settings_for = known
            return known[1]

    def token_ok(self, given: str | None) -> bool:
        return given is not None and secrets.compare_digest(
            given.encode("utf-8"), self._token.encode("utf-8")
        )

    def body_limit(self, parts: Sequence[str]) -> int:
        if tuple(parts) == ("dry-run", "upload"):
            return self.settings().max_upload_bytes
        return _JSON_BODY_LIMIT

    def refresh_flags(self) -> None:
        """Ask the service whether files changed and whether a recovery is needed.

        These checks may look at many files, so they run in the monitor thread and the
        handlers only read the last answer.
        """
        try:
            config = self._service.config()
            model = self._service.model()
            stale = model is not None and self._service.model_is_stale()
            recovery = config is not None and self._service.recovery_needed()
        except Exception as error:  # boundary: thread worker
            text = f"{type(error).__name__}: {error}"
            if text != self._flag_error:  # the same failure is logged once, not every 2 s
                self._flag_error = text
                _logger.exception("the check for changed files failed")
            return
        self._flag_error = ""
        self._flags = _Flags(model.model_hash if model is not None else None, stale, recovery)

    def monitor(self) -> None:
        """Body of the monitor thread: refresh the flags until the server closes."""
        while not self._closed.is_set():
            self.refresh_flags()
            self._wake.wait(self.settings().stale_check_interval_s)
            self._wake.clear()

    def close(self) -> None:
        self._closed.set()
        self._wake.set()

    def _current_flags(self, model: Model | None) -> tuple[bool, bool]:
        flags = self._flags
        model_hash = model.model_hash if model is not None else None
        if flags.model_hash != model_hash:
            self._wake.set()  # a new model: it is fresh until the monitor says otherwise
            return False, flags.recovery
        return flags.stale, flags.recovery

    # ---- jobs

    def _elapsed(self, job: JobView) -> float:
        now = time.monotonic()
        with self._lock:
            first, finished = self._job_times.get(job.id, (now, None))
            if job.state not in ACTIVE_STATES and finished is None:
                finished = now
                self._wake.set()  # a finished job may have changed stale / recovery
            self._job_times[job.id] = (first, finished)
            if len(self._job_times) > 500:
                for job_id in list(self._job_times)[:250]:
                    del self._job_times[job_id]
        return (finished if finished is not None else now) - first

    def _job_json(self, job: JobView, with_log: bool = True) -> dict[str, object]:
        log_file = current_log_file()
        return job_json(
            job, self._elapsed(job), str(log_file) if log_file is not None else None, with_log
        )

    def _active_jobs(self) -> list[JobView]:
        return [job for job in self._service.jobs() if job.state in ACTIVE_STATES]

    def _start(self, starter: Callable[[], str]) -> dict[str, object]:
        """Start one job unless another one is queued or running (one job at a time)."""
        with self._start_lock:
            active = self._active_jobs()
            if active:
                raise ApiError(
                    409,
                    "job-running",
                    f"Another job is running ({active[0].name}). "
                    "Wait until it has finished, or cancel it.",
                )
            job_id = starter()
            with self._lock:
                self._job_times[job_id] = (time.monotonic(), None)
        job = self._service.job(job_id)
        return {"job_id": job_id, "job": self._job_json(job) if job is not None else None}

    # ---- preconditions

    def _need_config(self) -> ProjectConfig:
        config = self._service.config()
        if config is None:
            raise ApiError(409, "no-repo", "No repo is open. Open a repo folder first.")
        return config

    def _need_model(self) -> Model:
        self._need_config()
        model = self._service.model()
        if model is None:
            raise ApiError(409, "no-model", "The repo is not loaded yet. Load it first.")
        return model

    def _refuse_in_recovery(self) -> None:
        if self._current_flags(self._service.model())[1]:
            raise ApiError(
                409,
                "recovery-needed",
                "An earlier apply did not finish. Roll back first; nothing else is allowed.",
            )

    def _accepted_count(self, outcome: ValidationOutcome | None) -> int:
        if outcome is None:
            return 0
        with self._lock:
            known = self._accepted
            if known is not None and known[0] is outcome:
                return known[1]
        count = sum(1 for r in outcome.results if r.status is RequestStatus.ACCEPTED)
        with self._lock:
            self._accepted = (outcome, count)
        return count

    def _apply_blockers(
        self,
        model: Model | None,
        accepted: int,
        stale: bool,
        recovery: bool,
        outcome: ValidationOutcome | None,
    ) -> list[str]:
        blockers: list[str] = []
        if model is None:
            blockers.append("The repo is not loaded.")
        if recovery:
            blockers.append("An earlier apply did not finish: roll back first.")
        if stale:
            blockers.append("Files changed on disk: reload, then validate again.")
        if outcome is None:
            blockers.append("Validate requests first.")
        elif accepted == 0:
            blockers.append("The last validation accepted no request.")
        elif outcome is self._applied:
            blockers.append("These requests were already sent to apply: validate again.")
        return blockers

    # ---- GET state

    def state(self) -> dict[str, object]:
        service = self._service
        config = service.config()
        model = service.model()
        stale, recovery = self._current_flags(model)
        outcome = service.last_validation()
        accepted = self._accepted_count(outcome)
        blockers = self._apply_blockers(model, accepted, stale, recovery, outcome)
        tables_status = "no-model"
        model_json: dict[str, object] | None = None
        if model is not None:
            ready = self._cache.get(model, config, self.settings().partner_limit)
            tables_status = "ready" if ready is not None else "preparing"
            model_json = {
                "hash": model.model_hash,
                "baseline_commit": model.baseline_commit,
                "autosar_schema": model.autosar_schema,
                "files": len(model.files),
                "findings": len(model.findings),
                "counts": {
                    "components": len(model.components),
                    "ports": len(model.ports),
                    "interfaces": len(model.interfaces),
                    "data_types": len(model.data_types),
                    "runnables": len(model.runnables),
                    "access_points": len(model.access_points),
                    "triggers": len(model.triggers),
                    "connectors": len(model.connectors),
                    "prototypes": len(model.prototypes),
                },
            }
        log_file = current_log_file()
        today = datetime.date.today().isoformat()
        report = service.last_report()
        return {
            "version": porttool.__version__,
            "repo": str(config.repo_root) if config is not None else None,
            "config_present": config is not None,
            "model_loaded": model is not None,
            "model": model_json,
            "tables": tables_status,
            "stale": stale,
            "recovery_needed": recovery,
            "jobs": [self._job_json(job, with_log=False) for job in self._active_jobs()],
            "validation": {
                "present": outcome is not None,
                "total": len(outcome.results) if outcome is not None else 0,
                "accepted": accepted,
                "can_apply": not blockers,
                "blockers": blockers,
            },
            "report_present": report is not None,
            "default_export_target": (
                str(config.repo_root / f"porttool_requests_{today}.xlsx")
                if config is not None
                else None
            ),
            "log_file": str(log_file) if log_file is not None else None,
            "max_upload_mb": self.settings().max_upload_mb,
            "max_upload_files": self.settings().max_upload_files,
        }

    # ---- POST actions

    def open_repo(self, payload: Mapping[str, object]) -> dict[str, object]:
        repo = _text(payload, "repo")
        if not repo:
            raise _bad_request("Enter the folder of the ECU repo.")
        config_file = _text(payload, "config_file")
        with self._start_lock:
            if self._active_jobs():
                raise ApiError(409, "job-running", "A job is running. Wait until it has finished.")
            findings = self._service.open_repo(
                Path(repo), Path(config_file) if config_file else None
            )
        self._wake.set()
        return {"findings": [finding_json(f) for f in findings], "state": self.state()}

    def start_load(self) -> dict[str, object]:
        self._need_config()
        self._refuse_in_recovery()
        return self._start(self._service.start_load)

    def start_export(self, payload: Mapping[str, object]) -> dict[str, object]:
        self._need_model()
        self._refuse_in_recovery()
        text = _text(payload, "target")
        if not text:
            raise _bad_request("Enter the path of the workbook to write.")
        target = Path(text)
        if not target.is_absolute():
            raise _bad_request(
                "The workbook path must be a full path, for example C:\\work\\requests.xlsx."
            )
        if target.suffix.lower() != ".xlsx":
            raise _bad_request("The workbook path must end with .xlsx.")
        if not target.parent.is_dir():
            raise _bad_request(f"The folder {target.parent} does not exist.")
        result = self._start(lambda: self._service.start_export(target))
        result["target"] = str(target)
        return result

    def start_dry_run_upload(self, content_type: str, body: bytes, name: str) -> dict[str, object]:
        self._need_model()
        self._refuse_in_recovery()
        if content_type.lower().startswith("multipart/form-data"):
            uploads = parse_multipart(content_type, body)
        else:
            uploads = [UploadedFile(name=name or "workbook.xlsx", data=body)]
        settings = self.settings()
        if not uploads:
            raise ApiError(400, "bad-upload", "No workbook was uploaded.")
        if len(uploads) > settings.max_upload_files:
            raise ApiError(
                400,
                "too-many-files",
                f"Too many workbooks in one import (limit {settings.max_upload_files}).",
            )
        for upload in uploads:
            check_xlsx(upload)

        def starter() -> str:
            previous = self._upload_dir
            if previous is not None:
                shutil.rmtree(previous, ignore_errors=True)  # no job is using them any more
            folder = self._tmp / "uploads" / secrets.token_hex(8)
            folder.mkdir(parents=True)
            names: dict[str, str] = {}
            paths: list[Path] = []
            for number, upload in enumerate(uploads, start=1):
                path = folder / safe_upload_name(upload.name, number)
                path.write_bytes(upload.data)
                paths.append(path)
                names[str(path)] = names[path.name] = display_name(upload.name)
            try:
                job_id = self._service.start_dry_run(paths)
            except Exception:
                shutil.rmtree(folder, ignore_errors=True)
                raise
            with self._lock:
                self._upload_dir = folder
                self._upload_names = names
            return job_id

        result = self._start(starter)
        result["files"] = [
            {"name": display_name(upload.name), "size": len(upload.data)} for upload in uploads
        ]
        return result

    def start_dry_run_rows(self, payload: object) -> dict[str, object]:
        self._need_model()
        self._refuse_in_recovery()
        rows = rows_from_json(payload, self.settings().max_form_rows)
        result = self._start(lambda: self._service.start_dry_run_rows(rows))
        result["rows"] = len(rows)
        return result

    def start_apply(self) -> dict[str, object]:
        model = self._need_model()
        stale, recovery = self._current_flags(model)
        outcome = self._service.last_validation()
        accepted = self._accepted_count(outcome)
        blockers = self._apply_blockers(model, accepted, stale, recovery, outcome)
        if blockers:
            code = "recovery-needed" if recovery else "stale" if stale else "nothing-to-apply"
            raise ApiError(409, code, " ".join(blockers))
        result = self._start(self._service.start_apply)
        self._applied = outcome
        result["accepted"] = accepted
        return result

    def start_rollback(self) -> dict[str, object]:
        self._need_config()
        if not self._service.recovery_needed():
            raise ApiError(409, "no-recovery", "There is nothing to roll back.")
        return self._start(self._service.start_rollback)

    def start_doctor(self) -> dict[str, object]:
        self._need_config()
        return self._start(self._service.start_doctor)

    def cancel(self, job_id: str) -> dict[str, object]:
        if self._service.job(job_id) is None:
            raise ApiError(404, "unknown-job", "There is no such job.")
        cancelled = self._service.cancel(job_id)
        job = self._service.job(job_id)
        return {"cancelled": cancelled, "job": self._job_json(job) if job is not None else None}

    def job(self, job_id: str) -> dict[str, object]:
        job = self._service.job(job_id)
        if job is None:
            raise ApiError(404, "unknown-job", "There is no such job.")
        return self._job_json(job)

    def jobs(self) -> dict[str, object]:
        return {"jobs": [self._job_json(job, with_log=False) for job in self._service.jobs()]}

    # ---- tables

    def _tables(self) -> tuple[str, ModelTables | None]:
        model = self._service.model()
        if model is None:
            return "no-model", None
        tables = self._cache.get(model, self._service.config(), self.settings().partner_limit)
        return ("ready", tables) if tables is not None else ("preparing", None)

    def _pending(self, tables: ModelTables) -> TableData:
        """The pending queue of the last dry run, or the one stored in the repo."""
        outcome = self._service.last_validation()
        queue = outcome.pending if outcome is not None else tables.pending_file
        with self._lock:
            known = self._pending_table
            if known is not None and known[0] is queue:
                return known[1]
        data = build_pending_table(queue)
        with self._lock:
            self._pending_table = (queue, data)
        return data

    def tables_list(self) -> dict[str, object]:
        status, tables = self._tables()
        listed: list[dict[str, object]] = []
        for definition in TABLE_DEFS:
            rows: int | None = None
            if tables is not None:
                if definition.name == "pending":
                    rows = len(self._pending(tables))
                else:
                    rows = len(tables.tables[definition.name])
            listed.append(
                {
                    "name": definition.name,
                    "title": definition.title,
                    "rows": rows,
                    "only": list(definition.only),
                    "columns": [
                        {"key": c.key, "header": c.header, "width": c.width, "numeric": c.numeric}
                        for c in definition.columns
                    ],
                }
            )
        return {
            "status": status,
            "model_hash": tables.model_hash if tables is not None else None,
            "tables": listed,
        }

    def table_page(self, name: str, params: Mapping[str, Sequence[str]]) -> dict[str, object]:
        definition = TABLE_DEF_BY_NAME.get(name)
        if definition is None:
            raise ApiError(404, "unknown-table", f"There is no table '{name[:60]}'.")
        try:
            query = parse_query(definition, params)
        except QueryError as error:
            raise ApiError(400, "bad-query", str(error)) from None
        status, tables = self._tables()
        result: dict[str, object] = {
            "status": status,
            "table": name,
            "model_hash": None,
            "offset": query.offset,
            "limit": query.limit,
            "total": 0,
            "filtered": 0,
            "rows": [],
        }
        if tables is None:
            return result
        data = self._pending(tables) if name == "pending" else tables.tables[name]
        started = time.perf_counter()
        page = data.query(query)
        result.update(
            model_hash=tables.model_hash,
            total=page.total,
            filtered=page.filtered,
            rows=page.rows,
            query_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )
        if name == "pending" and tables.pending_note:
            result["note"] = tables.pending_note
        return result

    def lists(self, params: Mapping[str, Sequence[str]]) -> dict[str, object]:
        status, tables = self._tables()
        result: dict[str, object] = {"status": status, "lists": {}}
        model = self._service.model()
        if tables is None or model is None:
            return result
        result["lists"] = tables.lists
        swc = (params.get("swc") or [""])[-1]
        if swc:
            path = tables.swc_paths.get(swc, swc)
            result["swc"] = {"name": swc, **swc_details(model, path)}
        prototype = (params.get("prototype") or [""])[-1]
        if prototype:
            names: set[str] = set()
            for type_path in tables.prototype_types.get(prototype, ()):
                names.update(port.name for port in model.ports_of(type_path))
            result["prototype"] = {"name": prototype, "ports": sorted(names)}
        return result

    def validation(self, params: Mapping[str, Sequence[str]]) -> dict[str, object]:
        status = (params.get("status") or [""])[-1] or None
        if status is not None and status not in {member.value for member in RequestStatus}:
            raise _bad_request(f"Unknown status '{status[:40]}'.")
        with self._lock:
            names = dict(self._upload_names)
        data = validation_json(
            self._service.last_validation(), self._service.last_plan(), status, names
        )
        data["apply"] = self.state()["validation"]
        return data

    def report(self) -> dict[str, object]:
        with self._lock:
            names = dict(self._upload_names)
        return report_json(self._service.last_report(), names)

    # ---- routing

    def _route(
        self,
        method: str,
        parts: Sequence[str],
        params: Mapping[str, Sequence[str]],
        body: bytes,
        content_type: str,
    ) -> Callable[[], dict[str, object]] | None:
        def payload() -> dict[str, object]:
            return _json_object(body)

        match (method, *parts):
            case ("GET", "state"):
                return self.state
            case ("GET", "jobs"):
                return self.jobs
            case ("GET", "jobs", job_id):
                return lambda: self.job(job_id)
            case ("GET", "tables"):
                return self.tables_list
            case ("GET", "tables", name):
                return lambda: self.table_page(name, params)
            case ("GET", "validation"):
                return lambda: self.validation(params)
            case ("GET", "report"):
                return self.report
            case ("GET", "lists"):
                return lambda: self.lists(params)
            case ("GET", "form"):
                return form_schema
            case ("POST", "open-repo"):
                return lambda: self.open_repo(payload())
            case ("POST", "load"):
                return self.start_load
            case ("POST", "export"):
                return lambda: self.start_export(payload())
            case ("POST", "dry-run", "upload"):
                name = (params.get("name") or [""])[-1]
                return lambda: self.start_dry_run_upload(content_type, body, name)
            case ("POST", "dry-run", "rows"):
                return lambda: self.start_dry_run_rows(payload())
            case ("POST", "apply"):
                return self.start_apply
            case ("POST", "rollback"):
                return self.start_rollback
            case ("POST", "doctor"):
                return self.start_doctor
            case ("POST", "jobs", job_id, "cancel"):
                return lambda: self.cancel(job_id)
        return None

    def api(
        self,
        method: str,
        parts: Sequence[str],
        params: Mapping[str, Sequence[str]],
        body: bytes,
        content_type: str,
    ) -> dict[str, object]:
        handler = self._route(method, parts, params, body, content_type)
        if handler is None:
            other = "POST" if method == "GET" else "GET"
            if self._route(other, parts, params, b"", "") is not None:
                raise ApiError(405, "method-not-allowed", f"Use {other} for this address.")
            raise ApiError(404, "not-found", "There is no such API address.")
        return handler()


def _json_object(body: bytes) -> dict[str, object]:
    if not body.strip():
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ApiError(400, "bad-json", "The request body is not valid JSON.") from None
    if not isinstance(data, dict):
        raise ApiError(400, "bad-json", "The request body must be a JSON object.")
    return cast(dict[str, object], data)


def status_for(error: PortToolError) -> int:
    """HTTP status of a ``PortToolError`` raised by the service."""
    if isinstance(error, ConfigError | WorkbookError):
        return 400
    if isinstance(error, RepoStateError | JobCancelled):
        return 409
    return 500


# --------------------------------------------------------------------------- HTTP


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True
    #: Off on purpose: on Windows this option would let a second program share the port.
    allow_reuse_address = False
    request_queue_size = 64

    def __init__(self, address: tuple[str, int], app: GuiApp) -> None:
        self.app = app
        super().__init__(address, _Handler)

    def server_bind(self) -> None:
        # Windows only: nobody else may bind the same port while it is in use.
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        # Not HTTPServer.server_bind: it calls socket.getfqdn, which can take seconds.
        socketserver.TCPServer.server_bind(self)
        host, port = self.socket.getsockname()[:2]
        self.server_name = str(host)
        self.server_port = int(port)

    def handle_error(self, request: object, client_address: object) -> None:
        _logger.debug("connection from %s ended with an error", client_address, exc_info=True)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60.0  # an idle keep-alive connection is closed after a minute
    _body_read = True

    def version_string(self) -> str:
        return "PortTool"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - base class name
        # Never the query string: the first request carries the session token.
        _logger.debug("http %s", (format % args).split("?", 1)[0])

    @property
    def _app(self) -> GuiApp:
        return cast(_HttpServer, self.server).app

    @property
    def _app_port(self) -> int:
        return cast(_HttpServer, self.server).server_port

    def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _other(self) -> None:
        self._dispatch(self.command)

    do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _other  # noqa: N815

    def _dispatch(self, method: str) -> None:
        self._body_read = self._declared_length() in (None, 0)
        try:
            self._handle(method)
        except ApiError as error:
            self._send_error_json(error.status, error.code, error.message)
        except PortToolError as error:
            _logger.info("request failed: %s", error, exc_info=True)
            self._send_error_json(status_for(error), error.code, error.user_message)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True  # the browser went away: nothing to answer
        except Exception:  # boundary: HTTP request handler
            _logger.exception("unexpected error in %s %s", method, self.path.split("?", 1)[0])
            try:
                self._send_error_json(500, "internal", f"Unexpected error. {_log_hint()}")
            except OSError:
                self.close_connection = True

    def _declared_length(self) -> int | None:
        text = self.headers.get("Content-Length")
        if text is None:
            return None
        try:
            length = int(text)
        except ValueError:
            return -1
        return length if length >= 0 else -1

    def _read_body(self, limit: int) -> bytes:
        length = self._declared_length()
        if length is None:
            raise ApiError(411, "length-required", "The request has no Content-Length.")
        if length < 0:
            raise ApiError(400, "bad-request", "The Content-Length of the request is not valid.")
        if length > limit:
            # Read and drop a moderately oversized body so the browser receives the answer
            # instead of a broken connection. Anything larger ends the connection.
            if length <= min(8 * limit, _DRAIN_LIMIT):
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 64 * 1024))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self._body_read = remaining == 0
            megabytes = max(1, limit // (1024 * 1024))
            raise ApiError(413, "too-large", f"The request is too large (limit {megabytes} MB).")
        body = self.rfile.read(length) if length else b""
        if len(body) != length:
            raise ApiError(400, "bad-request", "The request body is incomplete.")
        self._body_read = True
        return body

    def _host_ok(self) -> bool:
        port = self._app_port
        host = (self.headers.get("Host") or "").strip().lower()
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self._app_port
        return origin.strip().lower() in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _handle(self, method: str) -> None:
        if not self._host_ok():
            raise ApiError(403, "bad-host", "This address is not served by PortTool.")
        if not self._origin_ok():
            raise ApiError(403, "bad-origin", "Requests from other pages are refused.")
        split = urlsplit(self.path)
        path = unquote(split.path)
        if path.startswith("/api/"):
            app = self._app
            if not app.token_ok(self.headers.get(TOKEN_HEADER)):
                raise ApiError(
                    403,
                    "bad-token",
                    "The session token is missing or wrong. Open the page again with the "
                    "link printed by 'porttool gui'.",
                )
            if method not in ("GET", "POST"):
                raise ApiError(405, "method-not-allowed", "Only GET and POST are supported.")
            parts = path[len("/api/") :].split("/")
            params = parse_qs(split.query, keep_blank_values=True)
            body = self._read_body(app.body_limit(parts)) if method == "POST" else b""
            content_type = self.headers.get("Content-Type") or ""
            self._send_json(200, app.api(method, parts, params, body, content_type))
            return
        if method != "GET":
            raise ApiError(405, "method-not-allowed", "Only GET is supported here.")
        self._send_static(path)

    def _send_static(self, path: str) -> None:
        if path == "/favicon.ico":
            self._send(204, "image/x-icon", b"")
            return
        if path == "/":
            name = "index.html"
        elif path.startswith("/static/"):
            name = path[len("/static/") :]
        else:
            raise ApiError(404, "not-found", "There is no such page.")
        if _STATIC_NAME.fullmatch(name) is None:  # one flat folder: no way out of it
            raise ApiError(404, "not-found", "There is no such page.")
        try:
            data = (_STATIC_DIR / name).read_bytes()
        except OSError:
            raise ApiError(404, "not-found", "There is no such page.") from None
        self._send(200, _CONTENT_TYPES[Path(name).suffix], data)

    def _send_json(self, status: int, payload: object) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        self._send(status, "application/json; charset=utf-8", data.encode("utf-8"))

    def _send_error_json(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _send(self, status: int, content_type: str, data: bytes) -> None:
        if not self._body_read:
            self.close_connection = True  # an unread request body would corrupt the next request
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", _CSP)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


# --------------------------------------------------------------------------- public API


class ServerHandle:
    """A running web interface. ``close()`` stops it and removes its temporary folder."""

    def __init__(
        self,
        httpd: _HttpServer,
        app: GuiApp,
        token: str,
        tmp_dir: Path,
        threads: list[threading.Thread],
    ) -> None:
        self._httpd = httpd
        self._app = app
        self._tmp_dir = tmp_dir
        self._threads = threads
        self._closed = threading.Event()
        self._close_lock = threading.Lock()
        self.token = token
        self.port: int = httpd.server_port
        #: Address without the token (safe to log).
        self.base_url = f"http://127.0.0.1:{self.port}/"
        #: Address to open in the browser: carries the session token.
        self.url = f"{self.base_url}?token={token}"

    @property
    def temp_dir(self) -> Path:
        return self._tmp_dir

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the server is closed (or ``timeout`` seconds). True when closed."""
        return self._closed.wait(timeout)

    def close(self) -> None:
        """Stop serving. Safe to call twice. Does not close the service."""
        with self._close_lock:
            if self._closed.is_set():
                return
            self._app.close()
            self._httpd.shutdown()
            self._httpd.server_close()
            for thread in self._threads:
                thread.join(timeout=5.0)
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            if self._tmp_dir.exists():
                _logger.warning("the temporary folder %s could not be removed", self._tmp_dir)
            self._closed.set()

    def __enter__(self) -> ServerHandle:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def open_in_browser(url: str) -> bool:
    """Open the default browser. False (and a log line) when that is not possible."""
    try:
        opened = webbrowser.open(url)
    except (webbrowser.Error, OSError) as error:
        _logger.warning("the browser could not be opened: %s", error)
        return False
    if not opened:
        _logger.warning("no browser could be opened; open the printed address by hand")
    return opened


def serve(
    service: AppService,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    token: str | None = None,
    settings: GuiSettings | None = None,
) -> ServerHandle:
    """Start the web interface for ``service`` and return at once.

    The server runs in its own threads. ``port`` 0 picks a free port. ``settings`` fixes
    the ``[gui]`` settings; by default they are read from the config of the open repo.
    Raises ``GuiError`` when the port cannot be used.
    """
    if host not in ("127.0.0.1", "localhost"):
        raise GuiError("PortTool serves its page on this computer only (127.0.0.1).")
    if not 0 <= port <= 65535:
        raise GuiError(f"{port} is not a valid port number (0 to 65535).")
    session_token = token or secrets.token_urlsafe(32)
    tmp_dir = Path(tempfile.mkdtemp(prefix="porttool-gui-"))
    app = GuiApp(service, session_token, tmp_dir, settings)
    try:
        httpd = _HttpServer(("127.0.0.1", port), app)
    except OSError as error:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise GuiError(
            f"The web interface could not listen on 127.0.0.1:{port} "
            f"({error.strerror or error}). The port may be in use: choose another one with "
            "--port, or leave --port out to pick a free one.",
            detail=str(error),
        ) from error
    threads = [
        threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="porttool-gui-http",
            daemon=True,
        ),
        threading.Thread(target=app.monitor, name="porttool-gui-monitor", daemon=True),
    ]
    for thread in threads:
        thread.start()
    handle = ServerHandle(httpd, app, session_token, tmp_dir, threads)
    _logger.info("web interface listening on %s", handle.base_url)
    if open_browser:
        open_in_browser(handle.url)
    return handle


def run_forever(handle: ServerHandle) -> None:
    """Block until Ctrl+C (or until the handle is closed elsewhere), then close the server."""
    try:
        while not handle.wait(0.5):  # short waits: Ctrl+C is noticed on Windows too
            pass
    except KeyboardInterrupt:
        _logger.info("stopping the web interface")
    finally:
        handle.close()
