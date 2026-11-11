"""Unit tests of the building blocks: file access, XML style, the scanner, path logic."""

from __future__ import annotations

import os
import resource
import time
from pathlib import Path

import pytest

from porttool.discovery import fsio
from porttool.discovery.arxml_scan import scan_arxml, uuid_format
from porttool.discovery.context import Context
from porttool.discovery.fsio import ExcludeMatcher, atomic_write_bytes, io_path, walk_repo
from porttool.discovery.names import Names
from porttool.discovery.sections import (
    naming_pattern,
    safe_schema_location,
    safe_start_tag,
    schema_version_hint,
)
from porttool.discovery.workspace import DiskIndex, normalize_path, resolve_value
from porttool.discovery.xmlfeed import XmlSink, feed_xml
from porttool.discovery.xmlstyle import (
    StyleCollector,
    analyze_style,
    detect_indent,
    sniff_encoding,
)
from tests.unit.discovery import synth
from tests.unit.discovery.conftest import Runner

# ---- long paths (pure text logic; the Windows API behaviour is unverified here)


def test_io_path_adds_the_extended_prefix_only_to_long_windows_paths() -> None:
    short = "C:\\repo\\file.arxml"
    assert io_path(short) == short
    long_drive = "C:\\repo\\" + "folder\\" * 40 + "file.arxml"
    assert io_path(long_drive) == "\\\\?\\" + long_drive
    assert io_path("C:/repo/" + "folder/" * 40 + "f.arxml").startswith("\\\\?\\C:\\repo\\folder\\")
    long_unc = "\\\\server\\share\\" + "folder\\" * 40 + "file.arxml"
    assert io_path(long_unc) == "\\\\?\\UNC\\server\\share\\" + "folder\\" * 40 + "file.arxml"
    assert io_path("\\\\?\\" + long_drive) == "\\\\?\\" + long_drive
    long_posix = "/tmp/" + "folder/" * 40 + "file.arxml"
    assert io_path(long_posix) == long_posix


def test_normalize_path_handles_drives_unc_dots_and_mixed_separators() -> None:
    assert normalize_path("C:\\repo\\Config\\..\\Sip\\.\\x.arxml") == "C:/repo/Sip/x.arxml"
    assert normalize_path("c:/a//b/") == "c:/a/b"
    assert normalize_path("\\\\server\\share\\a\\..\\b") == "//server/share/b"
    assert normalize_path("/a/b/../../../c") == "/c"
    assert normalize_path("..\\..\\x") == "../../x"
    assert normalize_path("a/./b/../c") == "a/c"


def test_resolve_value_compares_without_case_and_flags_variables(repo: Path) -> None:
    walk = walk_repo(str(repo), ExcludeMatcher(()))
    index = DiskIndex(str(repo), walk)
    base = normalize_path(str(repo / "Config"))
    found = resolve_value("/x#text", "devws\\COMPONENTTYPES\\swcalpha.ARXML", base, index)
    assert found.status == "file"
    assert found.rel == "Config/DevWs/ComponentTypes/SwcAlpha.arxml"  # as spelled on disk
    assert resolve_value("/x", "DevWs", base, index).status == "dir"
    assert resolve_value("/x", "..\\..\\outside.arxml", base, index).rel is None
    assert resolve_value("/x", "$(Root)\\a.arxml", base, index).status == "variable"
    absolute = resolve_value("/x", str(repo / "EcuProj.dcf"), base, index)
    assert absolute.absolute and absolute.status == "file" and absolute.rel == "EcuProj.dcf"
    assert resolve_value("/x", "C:\\nowhere\\x.arxml", base, index).status == "missing"


# ---- walking and writing


def test_exclude_matcher_names_paths_and_double_star() -> None:
    matcher = ExcludeMatcher(["GenData", "*.bak", "Config/Sip/**", "a/**/c.txt", "  "])
    assert matcher.matches("Appl/GenData") and matcher.matches("gendata")
    assert matcher.matches("x/y/file.BAK")
    assert matcher.matches("Config/Sip") and not matcher.matches("Other/Config/Sip")
    assert matcher.matches("a/c.txt") and matcher.matches("a/b/b2/c.txt")
    assert not matcher.matches("Appl/GenDataX") and not matcher.matches("file.bak2")


def test_walk_reports_unreadable_folders_and_does_not_follow_links(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.symlink(repo / "Config", repo / "LinkToConfig")
    real = os.scandir

    def scandir(path: str):  # type: ignore[no-untyped-def]
        if str(path).endswith("System"):
            raise PermissionError("denied")
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    walk = walk_repo(str(repo), ExcludeMatcher([".git"]))
    assert walk.errors == [("Config/System", "PermissionError")]
    assert not any(entry.rel.startswith("LinkToConfig") for entry in walk.files)
    assert "Config/DevWs/ComponentTypes/SwcAlpha.arxml" in [entry.rel for entry in walk.files]


def test_atomic_write_retries_a_sharing_violation_then_gives_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "out.txt"
    calls = {"count": 0}
    real = os.replace

    def flaky(source: str, destination: str) -> None:
        calls["count"] += 1
        if calls["count"] <= 2:
            raise PermissionError("sharing violation")
        real(source, destination)

    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    atomic_write_bytes(str(target), b"data")
    assert target.read_bytes() == b"data" and calls["count"] == 3
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out.txt"]

    def always(_source: str, _destination: str) -> None:
        raise PermissionError("still locked")

    monkeypatch.setattr(os, "replace", always)
    with pytest.raises(PermissionError):
        atomic_write_bytes(str(target), b"new")
    assert target.read_bytes() == b"data"  # the old file is intact
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out.txt"]  # no temp file left


def test_atomic_write_text_normalises_to_the_requested_line_ending(tmp_path: Path) -> None:
    fsio.atomic_write_text(str(tmp_path / "r.txt"), "a\r\nb\nc\r", newline="\r\n")
    assert (tmp_path / "r.txt").read_bytes() == b"a\r\nb\r\nc\r\n"
    assert fsio.iso_utc(0) == "1970-01-01T00:00:00Z"


# ---- XML style


def test_style_facts_of_a_crlf_tab_document_with_prefix_and_single_quotes() -> None:
    data = (
        b"<?xml version='1.0' encoding='UTF-8' standalone='no'?>\r\n"
        b"<!-- head -->\r\n"
        b"<AR:AUTOSAR xmlns:AR='http://autosar.org/schema/r4.0'\r\n"
        b"\txsi:schemaLocation='http://autosar.org/schema/r4.0 AUTOSAR_00046.xsd'>\r\n"
        b"\t<AR:A>\r\n\t\t<AR:B/>\r\n\t</AR:A>\r\n</AR:AUTOSAR>"
    )
    style = analyze_style(data)
    assert style.declaration == "<?xml version='1.0' encoding='UTF-8' standalone='no'?>"
    assert (style.line_endings, style.crlf, style.lf, style.cr) == ("crlf", 7, 0, 0)
    assert style.indent == "1 tab" and style.trailing_newline == "none"
    assert style.root_prefix == "AR" and style.root_tag == "AUTOSAR"
    assert style.namespace == "http://autosar.org/schema/r4.0"
    assert style.schema_location == "http://autosar.org/schema/r4.0 AUTOSAR_00046.xsd"
    assert style.head_comments == 1 and style.attribute_quote == "single"
    assert schema_version_hint(style.namespace, style.schema_location) == "00046"
    assert schema_version_hint("http://autosar.org/schema/r4.0", None) == "r4.0"


def test_style_counts_mixed_line_endings_across_chunk_borders() -> None:
    style = StyleCollector()
    data = b"<a>\r\n<b/>\n<c/>\r<d/>\r\n</a>\r\n"
    # the first chunk is padded past the sniffing size and ends between CR and LF
    chunks = [b" " * 4096 + data[:4], data[4:5], data[5:]]
    assert feed_xml(chunks, XmlSink(), style) is None
    facts = style.result()
    assert (facts.crlf, facts.lf, facts.cr, facts.line_endings) == (3, 1, 1, "mixed")
    assert analyze_style(b"<a/>").line_endings == "none"
    assert analyze_style(b"<a>\r<b/>\r</a>\r").trailing_newline == "cr"


def test_detect_indent_variants() -> None:
    assert detect_indent("<a>\n    <b>\n        <c/>\n    </b>\n</a>\n") == "4 spaces"
    assert detect_indent("<a>\n <b/>\n</a>\n") == "1 space"
    assert detect_indent("<a>\n\t<b>\n  <c/>\n\t</b>\n</a>\n") == "mixed"
    assert detect_indent("<a><b/></a>") == "none"
    assert detect_indent("<a>\n<b/>\n</a>\n") == "none"


def test_sniff_encoding_variants() -> None:
    assert sniff_encoding(b"<a/>").codec == "utf-8"
    latin = sniff_encoding(b'<?xml version="1.0" encoding="ISO-8859-1"?><a/>')
    assert (latin.declared, latin.codec, latin.is_utf8) == ("ISO-8859-1", "iso-8859-1", False)
    assert sniff_encoding("<a/>".encode("utf-16-be")).codec == "utf-16-be"
    assert sniff_encoding(b"\xff\xfe\x00\x00<\x00\x00\x00").bom == "utf-32-le"
    assert sniff_encoding(b"\xfe\xff\x00<").bom == "utf-16-be"


def test_schema_location_and_root_tag_are_made_safe_only_when_anonymizing() -> None:
    plain, anonymous = Names(), Names(anonymize=True, key=b"k")
    location = "http://autosar.org/schema/r4.0 ..\\Secret\\AUTOSAR_4-2-2.xsd"
    assert safe_schema_location(plain, location) == location
    shown = safe_schema_location(anonymous, location)
    assert shown is not None and shown.endswith("/AUTOSAR_4-2-2.xsd") and "Secret" not in shown
    assert safe_schema_location(anonymous, "http://x.example/s Custom.xsd").count("N") == 2
    tag = '<DCF NAME="SecretEcu" VERSION="4.2">'
    assert safe_start_tag(plain, tag) == tag
    hidden = safe_start_tag(anonymous, tag)
    assert hidden is not None and "SecretEcu" not in hidden and 'VERSION="4.2"' in hidden


# ---- the ARXML scanner


def test_scanner_handles_namespace_prefix_nested_packages_and_latin1() -> None:
    text = (
        '<?xml version="1.0" encoding="ISO-8859-1"?>\n'
        '<AR:AUTOSAR xmlns:AR="http://autosar.org/schema/r4.0"><AR:AR-PACKAGES><AR:AR-PACKAGE>'
        "<AR:SHORT-NAME>Outer</AR:SHORT-NAME><AR:AR-PACKAGES><AR:AR-PACKAGE>"
        "<AR:SHORT-NAME>Inner</AR:SHORT-NAME><AR:ELEMENTS><AR:UNIT>"
        "<AR:SHORT-NAME>Grad</AR:SHORT-NAME><AR:DESC><AR:L-2>Größe aus MATLAB</AR:L-2></AR:DESC>"
        "<AR:EMPTY></AR:EMPTY></AR:UNIT></AR:ELEMENTS></AR:AR-PACKAGE></AR:AR-PACKAGES>"
        "</AR:AR-PACKAGE></AR:AR-PACKAGES></AR:AUTOSAR>"
    )
    facts = scan_arxml("x.arxml", 1, [text.encode("iso-8859-1")])
    assert facts.ok and facts.root_tag == "AUTOSAR"
    assert [(o.kind, o.package, o.name, o.path) for o in facts.objects] == [
        ("UNIT", "/Outer/Inner", "Grad", "/Outer/Inner/Grad")
    ]
    assert facts.objects[0].sections == ("SHORT-NAME", "DESC", "EMPTY")
    assert facts.mentions == {("MATLAB", "DESC"): 1}
    assert facts.open_close_empty == 1 and facts.self_closing == 0
    assert facts.style is not None and facts.style.root_prefix == "AR"


def test_scanner_keeps_partial_facts_of_a_truncated_file_and_reports_read_errors() -> None:
    whole = synth.SWC_ALPHA.encode()
    facts = scan_arxml("x.arxml", len(whole), [whole[:1500]])
    assert facts.problem is not None and facts.problem.kind == "malformed"
    assert facts.tag_counts["P-PORT-PROTOTYPE"] == 1 and facts.uuid_count >= 2

    def failing():  # type: ignore[no-untyped-def]
        yield whole[:5000]
        raise OSError("device not ready")

    broken = scan_arxml("x.arxml", len(whole), failing())
    assert broken.problem is not None and broken.problem.kind == "unreadable"
    assert broken.problem.message == "OSError" and broken.style is not None
    wrong = scan_arxml("x.arxml", 1, [b'<?xml version="1.0" encoding="UTF-8"?><a>\xff</a>'])
    assert wrong.problem is not None and wrong.problem.kind == "malformed"
    wide = scan_arxml("x.arxml", 1, ['<?xml version="1.0"?><a/>'.encode("utf-16")[:-1]])
    assert wide.problem is not None and wide.problem.kind == "encoding"


def test_uuid_formats() -> None:
    assert uuid_format("0f8fad5b-d9cb-469f-a165-70867728950e") == ("8-4-4-4-12 lower", "4")
    assert uuid_format("0F8FAD5B-D9CB-169F-A165-70867728950E") == ("8-4-4-4-12 upper", "1")
    assert uuid_format("{0f8fad5b-d9cb-469f-a165-70867728950E}") == ("{}8-4-4-4-12 mixed", "4")
    assert uuid_format("DCE:0f8fad5b-d9cb-469f-a165-70867728950e")[0] == "DCE:8-4-4-4-12 lower"
    assert uuid_format("0f8fad5bd9cb469fa16570867728950e") == ("32 lower", "4")
    assert uuid_format("12345678-1234-4234-8234-123456789012")[0] == "8-4-4-4-12 digits"
    assert uuid_format("not a uuid") == ("other", "?") and uuid_format("") == ("other", "?")


def test_naming_pattern_between_file_stem_and_short_name() -> None:
    assert naming_pattern("SwcA", "SwcA") == ("{name}", "", "")
    assert naming_pattern("swca", "SwcA") == ("{name} (other case)", "", "")
    assert naming_pattern("ct_SwcA_swc", "SwcA") == ("{prefix}{name}{suffix}", "ct_", "_swc")
    assert naming_pattern("SwcA_types", "SwcA") == ("{name}{suffix}", "", "_types")
    assert naming_pattern("CT_SWCA", "SwcA")[0] == "{prefix}{name} (other case)"
    assert naming_pattern("Other", "SwcA") == ("unrelated", "", "")
    assert naming_pattern("Other", "") == ("unrelated", "", "")


def test_storage_map_reports_objects_defined_in_two_files_and_affix_naming(
    repo: Path, discover: Runner
) -> None:
    synth.write(repo, "Split/SwcAlpha_ports.arxml", synth.SWC_ALPHA)
    storage = discover(repo).report["storage_map"]
    split = storage["split_objects"]
    assert split["count"] == 1
    assert split["samples"][0]["path"] == "/ComponentTypes/SwcAlpha"
    assert len(split["samples"][0]["files"]) == 2
    patterns = {
        row["pattern"]: row["files"]
        for row in storage["kinds"]["APPLICATION-SW-COMPONENT-TYPE"]["file_naming"]
    }
    assert patterns == {"{name}": 3, "{name}_ports": 1}


def test_context_counts_findings_by_severity() -> None:
    ctx = Context(names=Names(anonymize=True, key=b"k"))
    ctx.step = "s"
    ctx.warning("W", "w", file="Dir/File.arxml")
    ctx.info("I", "i")
    ctx.error("E", "e")
    assert ctx.count_by_severity() == {"info": 1, "warning": 1, "error": 1}
    shown = ctx.findings_json()[0]
    file_shown = str(shown["file"])
    assert shown["step"] == "s" and file_shown.endswith(".arxml") and "Dir" not in file_shown


# ---- a large file is streamed, not loaded


@pytest.mark.slow
def test_60_mb_file_is_streamed_within_bounded_memory(tmp_path: Path, discover: Runner) -> None:
    repo = tmp_path / "big"
    repo.mkdir()
    target = repo / "Big.arxml"
    ports = "".join(
        f'<P-PORT-PROTOTYPE UUID="{j:032x}"><SHORT-NAME>Pp{j}</SHORT-NAME>'
        '<PROVIDED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/PortInterfaces/If'
        "</PROVIDED-INTERFACE-TREF></P-PORT-PROTOTYPE>\n"
        for j in range(20)
    )
    count = 0
    with open(target, "wb") as handle:
        handle.write(synth.HEADER.encode())
        handle.write(b"<AR-PACKAGES><AR-PACKAGE><SHORT-NAME>Big</SHORT-NAME><ELEMENTS>\n")
        while handle.tell() < 60 * 1024 * 1024:
            handle.write(
                f'<APPLICATION-SW-COMPONENT-TYPE UUID="{count:032x}"><SHORT-NAME>Swc{count}'
                f"</SHORT-NAME><PORTS>\n{ports}</PORTS></APPLICATION-SW-COMPONENT-TYPE>\n".encode()
            )
            count += 1
        handle.write(b"</ELEMENTS></AR-PACKAGE></AR-PACKAGES></AUTOSAR>\n")
    size = target.stat().st_size
    assert size > 60 * 1024 * 1024
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.monotonic()
    result = discover(repo)
    seconds = time.monotonic() - started
    grown_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024
    kinds = result.report["inventory"]["top_level_kinds"]
    assert kinds["APPLICATION-SW-COMPONENT-TYPE"]["objects"] == count
    assert result.report["inventory"]["tag_histogram"]["P-PORT-PROTOTYPE"]["count"] == count * 20
    assert result.report["inventory"]["problem_files"] == []
    print(f"\n60 MB scan: {size / 1e6:.1f} MB in {seconds:.1f} s, peak RSS grew {grown_mb:.0f} MB")
    assert grown_mb < 200, "a 60 MB file must not be held in memory as a tree"
    assert seconds < 120
