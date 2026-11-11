"""File discovery, exclude patterns, the .dcf comparison and the staleness check."""

from __future__ import annotations

from pathlib import Path

import pytest

from porttool.core.results import Severity
from porttool.reader import codes, dcf
from porttool.reader.discovery import compile_globs, discover, glob_to_regex, relative_path
from tests.unit.reader import samples
from tests.unit.reader.conftest import Repo, codes_of, findings_with
from tests.unit.reader.samples import CT, doc, package, swc


def one_swc(name: str) -> str:
    return doc(package("ComponentTypes", swc(name)))


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("**/GenData/**", "Appl/GenData/Rte.arxml", True),
        ("**/GenData/**", "GenData/Rte.arxml", True),
        ("**/GenData/**", "Appl/GenData", True),  # the folder itself: the walk is pruned
        ("**/GenData/**", "Appl/GenDataX/Rte.arxml", False),
        ("*.bak.arxml", "Config/Developer/Old.bak.arxml", True),  # no slash: any depth
        ("*.bak.arxml", "Config/Developer/Old.arxml", False),
        ("Backup", "Config/Backup/A.arxml", True),  # a folder name excludes what is below
        ("Config/Gen*/**", "Config/Generated/a/b.arxml", True),
        ("Config/Gen*/**", "Other/Config/Generated/b.arxml", False),  # anchored at the root
        ("Config/*/Old.arxml", "Config/A/Old.arxml", True),
        ("Config/*/Old.arxml", "Config/A/B/Old.arxml", False),  # * stays in one segment
        ("Config/**/Old.arxml", "Config/A/B/Old.arxml", True),
        ("Config/**/Old.arxml", "Config/Old.arxml", True),  # ** matches no folder too
        ("config/developer/swc?.arxml", "Config/Developer/Swc1.arxml", True),  # case ignored
        ("Config/Swc[12].arxml", "Config/Swc2.arxml", True),
        ("Config/Swc[!12].arxml", "Config/Swc2.arxml", False),
        ("Config\\Developer\\Old", "Config/Developer/Old/x.arxml", True),  # backslashes
        ("./Config/Old/", "Config/Old/x.arxml", True),
        ("a+b(c).arxml", "a+b(c).arxml", True),  # regex characters are literal
    ],
)
def test_exclude_pattern_semantics(pattern: str, path: str, expected: bool) -> None:
    assert compile_globs([pattern])(path) is expected, glob_to_regex(pattern)


def test_no_patterns_exclude_nothing() -> None:
    assert compile_globs([])("anything.arxml") is False
    assert compile_globs(["", "  "])("anything.arxml") is False


def test_discovery_loads_roots_minus_excludes_plus_ecuc(repo: Repo) -> None:
    repo.write("Config/Developer/A.arxml", one_swc("A"))
    repo.write("Config/Developer/Sub/B.ARXML", one_swc("B"))  # suffix in upper case
    repo.write("Config/Developer/Backup/A.arxml", one_swc("OldA"))
    repo.write("Config/Developer/notes.txt", "not arxml")
    repo.write("Config/Developer/.git/objects/X.arxml", one_swc("InGit"))
    repo.write("Config/GenData/Rte_swc.arxml", one_swc("Generated"))
    repo.write("Sip/BSWMD/Com_bswmd.arxml", one_swc("SipFile"))
    repo.write("Config/System/FlatExtract.arxml", one_swc("Flat"))
    repo.write("Config/System/FlatMap.arxml", one_swc("Map"))
    repo.write("Models/SwcM/arxml/SwcM.arxml", one_swc("SwcM"))
    repo.write("Config/ECUC/Ecu_ecuc.arxml", samples.ecuc_doc())
    config = repo.config(
        arxml_roots=(
            repo.root / "Config",
            repo.root / "Sip",
            repo.path("Models/SwcM/arxml/SwcM.arxml"),
        ),
        exclude_globs=("Backup",),
        gen_data=repo.root / "Config" / "GenData",
        sip_root=repo.root / "Sip",
        flat_extract=repo.path("Config/System/FlatExtract.arxml"),
        flat_map=repo.path("Config/System/FlatMap.arxml"),
        ecuc_file=repo.path("Config/ECUC/Ecu_ecuc.arxml"),
    )
    found = discover(config)
    assert [f.rel_path for f in found.files] == [
        "Config/Developer/A.arxml",
        "Config/Developer/Sub/B.ARXML",
        "Config/ECUC/Ecu_ecuc.arxml",
        "Models/SwcM/arxml/SwcM.arxml",
    ]
    assert [f.rel_path for f in found.files if f.is_ecuc] == ["Config/ECUC/Ecu_ecuc.arxml"]
    assert found.findings == ()
    model = repo.load(config)
    assert sorted(model.components) == [f"{CT}/A", f"{CT}/B", f"{CT}/SwcM"]
    assert model.tasks == frozenset({"Task_10ms", "Task_Event", "Task_Init"})


def test_ecuc_file_is_loaded_even_if_an_exclude_pattern_matches_it(repo: Repo) -> None:
    repo.write("Config/Developer/A.arxml", one_swc("A"))
    repo.write("Config/Developer/ECUC/Ecu_ecuc.arxml", samples.ecuc_doc())
    config = repo.config(
        exclude_globs=("ECUC",), ecuc_file=repo.path("Config/Developer/ECUC/Ecu_ecuc.arxml")
    )
    found = discover(config)
    assert [(f.rel_path, f.is_ecuc) for f in found.files] == [
        ("Config/Developer/A.arxml", False),
        ("Config/Developer/ECUC/Ecu_ecuc.arxml", True),
    ]


def test_ecuc_file_inside_a_root_is_loaded_once(repo: Repo) -> None:
    repo.write("Config/Developer/Ecu_ecuc.arxml", samples.ecuc_doc())
    config = repo.config(ecuc_file=repo.path("Config/Developer/Ecu_ecuc.arxml"))
    found = discover(config)
    assert [(f.rel_path, f.is_ecuc, f.in_roots) for f in found.files] == [
        ("Config/Developer/Ecu_ecuc.arxml", True, True)
    ]


def test_ecuc_split_over_a_folder(repo: Repo) -> None:
    """PENDING-DISCOVERY work-around: ``ecuc_file`` may name a folder of ECUC files."""
    vendor = "/MICROSAR"
    repo.write(
        "Config/ECUC/Os_ecuc.arxml",
        doc(
            package(
                "ActiveEcuC", samples.ecuc_module("Os", samples.ecuc_task("T1", vendor), vendor)
            )
        ),
    )
    repo.write(
        "Config/ECUC/Rte_ecuc.arxml",
        doc(
            package(
                "ActiveEcuC",
                samples.ecuc_module(
                    "Rte",
                    samples.ecuc_instance(
                        "Swc", samples.ecuc_mapping("Ev", "/P/Swc/Beh/Ev", "T1", 5, vendor), vendor
                    ),
                    vendor,
                ),
            )
        ),
    )
    model = repo.load(ecuc_file=repo.root / "Config" / "ECUC")
    assert model.tasks == frozenset({"T1"})
    mapping = model.task_of("/P/Swc/Beh/Ev")
    assert mapping is not None and (mapping.task, mapping.position) == ("T1", 5)
    assert codes_of(model) == [codes.ECUC_EVENT_UNKNOWN]


def test_missing_root_and_missing_ecuc_file_are_findings(repo: Repo) -> None:
    repo.write("Config/Developer/A.arxml", one_swc("A"))
    model = repo.load(
        arxml_roots=(repo.developer, repo.root / "DoesNotExist"),
        ecuc_file=repo.root / "NoEcuc.arxml",
    )
    missing = findings_with(model, codes.ROOT_MISSING)
    assert [(f.file, f.severity) for f in missing] == [
        ("DoesNotExist", Severity.ERROR),
        ("NoEcuc.arxml", Severity.ERROR),
    ]
    assert f"{CT}/A" in model.components


def test_folder_that_cannot_be_listed_is_a_finding(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.write("Config/Developer/A.arxml", one_swc("A"))
    repo.write("Config/Developer/Secret/B.arxml", one_swc("B"))
    import os

    real_scandir = os.scandir

    def scandir(path: str) -> object:
        if os.path.basename(path) == "Secret":
            raise PermissionError(13, "Permission denied", path)
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    model = repo.load()
    monkeypatch.undo()
    assert [f.file for f in findings_with(model, codes.FOLDER_UNREADABLE)] == [
        "Config/Developer/Secret"
    ]
    assert sorted(model.components) == [f"{CT}/A"]


def test_relative_path_outside_the_repo_stays_absolute(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    assert relative_path(repo_root / "a" / "b.arxml", repo_root) == "a/b.arxml"
    assert relative_path(repo_root, repo_root) == ""
    outside = tmp_path / "elsewhere" / "x.arxml"
    assert relative_path(outside, repo_root) == str(outside).replace("\\", "/")


# ---- .dcf


def dcf_text(*files: str) -> str:
    refs = "".join(f"<FILEREF><ARXML>{name}</ARXML></FILEREF>" for name in files)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><DCF ARSCHEMA="4.2.2"><NAME>Ecu</NAME>{refs}</DCF>'
    )


def test_dcf_lists_a_file_that_is_missing_on_disk(repo: Repo) -> None:
    repo.write("Config/Developer/A.arxml", one_swc("A"))
    repo.write("Config/Developer/Ecu.dcf", dcf_text("A.arxml", "ComponentTypes\\Gone.arxml"))
    model = repo.load(dcf_file=repo.path("Config/Developer/Ecu.dcf"))
    assert codes_of(model) == [codes.DCF_MISSING_ON_DISK]
    finding = model.findings[0]
    assert finding.severity is Severity.ERROR
    assert finding.file == "Config/Developer/ComponentTypes/Gone.arxml"
    assert f"{CT}/A" in model.components


def test_file_on_disk_that_the_dcf_does_not_list(repo: Repo) -> None:
    repo.write("Config/Developer/A.arxml", one_swc("A"))
    repo.write("Config/Developer/Extra/B.arxml", one_swc("B"))
    repo.write("Config/ECUC/Ecu_ecuc.arxml", samples.ecuc_doc())
    repo.write("Config/Developer/Ecu.dcf", dcf_text("A.arxml"))
    model = repo.load(
        dcf_file=repo.path("Config/Developer/Ecu.dcf"),
        ecuc_file=repo.path("Config/ECUC/Ecu_ecuc.arxml"),
    )
    not_listed = findings_with(model, codes.DCF_NOT_LISTED)
    # B is reported; the ECUC file is not expected in a Developer workspace
    assert [(f.file, f.severity) for f in not_listed] == [
        ("Config/Developer/Extra/B.arxml", Severity.WARNING)
    ]
    assert f"{CT}/B" in model.components  # it is still loaded


def test_dcf_lists_a_file_that_exists_but_is_not_loaded(repo: Repo) -> None:
    repo.write("Config/Developer/A.arxml", one_swc("A"))
    repo.write("Config/Developer/Backup/Old.arxml", one_swc("Old"))
    repo.write("Config/Shared/Lib.arxml", one_swc("Lib"))
    repo.write(
        "Config/Developer/Ecu.dcf", dcf_text("A.arxml", "Backup/Old.arxml", "../Shared/Lib.arxml")
    )
    model = repo.load(dcf_file=repo.path("Config/Developer/Ecu.dcf"), exclude_globs=("Backup",))
    not_loaded = findings_with(model, codes.DCF_NOT_LOADED)
    assert [f.file for f in not_loaded] == [
        "Config/Developer/Backup/Old.arxml",
        "Config/Shared/Lib.arxml",
    ]
    assert codes_of(model) == [codes.DCF_NOT_LOADED] * 2


def test_dcf_that_matches_the_disk_gives_no_finding_and_is_fingerprinted(repo: Repo) -> None:
    repo.write("Config/Developer/A.arxml", one_swc("A"))
    target = repo.write("Config/Developer/Ecu.dcf", dcf_text("A.arxml"))
    config = repo.config(dcf_file=target)
    model = repo.load(config)
    assert model.findings == ()
    assert sorted(model.files) == ["Config/Developer/A.arxml", "Config/Developer/Ecu.dcf"]
    assert repo.reader.is_stale(config, model) is False
    target.write_text(dcf_text("A.arxml", "New.arxml"))
    assert repo.reader.is_stale(config, model) is True


@pytest.mark.parametrize("content", ["this is not xml", ""])
def test_unreadable_dcf_is_a_finding(repo: Repo, content: str) -> None:
    repo.write("Config/Developer/A.arxml", one_swc("A"))
    repo.write("Config/Developer/Ecu.dcf", content)
    model = repo.load(dcf_file=repo.path("Config/Developer/Ecu.dcf"))
    assert codes_of(model) == [codes.DCF_UNREADABLE]
    assert model.findings[0].file == "Config/Developer/Ecu.dcf"
    assert f"{CT}/A" in model.components


def test_missing_dcf_is_a_finding(repo: Repo) -> None:
    repo.write("Config/Developer/A.arxml", one_swc("A"))
    model = repo.load(dcf_file=repo.path("Config/Developer/NoSuch.dcf"))
    assert codes_of(model) == [codes.DCF_UNREADABLE]


def test_dcf_seam_extracts_relative_arxml_paths_only(tmp_path: Path) -> None:
    """PENDING-DISCOVERY: the provisional rule of ``list_arxml_references``."""
    target = tmp_path / "Ecu.dcf"
    target.write_text(
        '<?xml version="1.0"?><DCF><!-- Comment.arxml -->'
        '<FILE PATH="Sub\\InAttribute.arxml"/>'
        "<FILE>  InText.arxml  </FILE>"
        "<FILE>InText.arxml</FILE>"  # listed twice: reported once
        "<FILE>UPPER.ARXML</FILE>"
        "<FILE>C:\\Absolute\\Drive.arxml</FILE>"
        "<FILE>/absolute/unix.arxml</FILE>"
        "<FILE>\\\\server\\share\\Unc.arxml</FILE>"
        "<FILE>http://example.invalid/Url.arxml</FILE>"
        "<FILE>Readme.txt</FILE>"
        "<NOTE>two\nlines.arxml</NOTE>"
        "</DCF>"
    )
    assert dcf.list_arxml_references(target) == (
        "Sub/InAttribute.arxml",
        "InText.arxml",
        "UPPER.ARXML",
    )
    target.write_text("<DCF>")
    with pytest.raises(dcf.DcfFormatError):
        dcf.list_arxml_references(target)


# ---- staleness


def test_is_stale_detects_change_addition_and_removal(repo: Repo) -> None:
    samples.write_sample_repo(repo.root)
    config = repo.config(ecuc_file=repo.path(samples.F_ECUC))
    model = repo.load(config)
    reader = repo.reader
    assert reader.is_stale(config, model) is False

    target = repo.path(samples.F_SWC_M)
    original = target.read_bytes()
    target.write_bytes(original + b"\n")  # size changes
    assert reader.is_stale(config, model) is True
    target.write_bytes(original)
    import os

    old = model.files[samples.F_SWC_M]
    os.utime(target, ns=(old.mtime_ns, old.mtime_ns))
    assert reader.is_stale(config, model) is False

    os.utime(target, ns=(old.mtime_ns + 1_000_000_000, old.mtime_ns + 1_000_000_000))
    assert reader.is_stale(config, model) is True  # same size, newer time
    os.utime(target, ns=(old.mtime_ns, old.mtime_ns))

    added = repo.write("Config/Developer/New.arxml", one_swc("New"))
    assert reader.is_stale(config, model) is True
    added.unlink()
    assert reader.is_stale(config, model) is False

    repo.path(samples.F_ECUC).write_bytes(samples.ecuc_doc("/AUTOSAR/EcucDefs").encode())
    assert reader.is_stale(config, model) is True  # the ECUC file counts too

    fresh = repo.load(config)
    assert reader.is_stale(config, fresh) is False
    target.unlink()
    assert reader.is_stale(config, fresh) is True
    # files that are not loaded do not make the model stale
    again = repo.load(config)
    repo.write("Config/Developer/notes.txt", "hello")
    repo.write("Elsewhere/X.arxml", one_swc("X"))
    assert reader.is_stale(config, again) is False


def test_is_stale_does_not_read_file_contents(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    samples.write_sample_repo(repo.root, ecuc=False)
    target = repo.write("Config/Developer/Ecu.dcf", dcf_text())
    config = repo.config(dcf_file=target)
    model = repo.load(config)
    import builtins

    def no_open(*args: object, **kwargs: object) -> object:
        raise AssertionError("is_stale must only stat files")

    monkeypatch.setattr(builtins, "open", no_open)
    monkeypatch.setattr(Path, "read_bytes", no_open)
    try:
        stale = repo.reader.is_stale(config, model)
    finally:
        monkeypatch.undo()
    assert stale is False


def test_ecuc_definitions_with_the_autosar_standard_prefix(repo: Repo) -> None:
    samples.write_sample_repo(repo.root, ecuc=False)
    repo.write(samples.F_ECUC, samples.ecuc_doc("/AUTOSAR/EcucDefs"))
    model = repo.load(ecuc_file=repo.path(samples.F_ECUC))
    assert model.tasks == frozenset({"Task_10ms", "Task_Event", "Task_Init"})
    assert len(model.task_mappings) == 3 and model.findings == ()


def test_ecuc_mapping_to_unknown_event_unknown_task_and_conflicting_tasks(repo: Repo) -> None:
    samples.write_sample_repo(repo.root, ecuc=False)
    vendor = "/MICROSAR"
    on_speed = f"{samples.BEH_B}/DRT_RunB_OnSpeed"
    rte = samples.ecuc_module(
        "Rte",
        samples.ecuc_instance(
            "SwcB",
            samples.ecuc_mapping("M1", on_speed, "Task_Event", 1, vendor)
            + samples.ecuc_mapping("M2", f"{samples.BEH_B}/NoSuchEvent", "Task_Event", 2, vendor)
            + samples.ecuc_mapping(
                "M3", f"{samples.BEH_B}/BGT_RunB_Background", "Task_Gone", 3, vendor
            ),
            vendor,
        )
        + samples.ecuc_instance(
            "SwcB1", samples.ecuc_mapping("M1", on_speed, "Task_Other", 1, vendor), vendor
        ),
        vendor,
    )
    os_module = samples.ecuc_module(
        "Os",
        samples.ecuc_task("Task_Event", vendor) + samples.ecuc_task("Task_Other", vendor),
        vendor,
    )
    repo.write(samples.F_ECUC, doc(package("ActiveEcuC", os_module, rte)))
    model = repo.load(ecuc_file=repo.path(samples.F_ECUC))
    assert sorted(codes_of(model)) == [
        codes.ECUC_EVENT_UNKNOWN,
        codes.ECUC_TASK_UNKNOWN,
        codes.ECUC_MAPPING_CONFLICT,
    ]
    assert all(f.file == samples.F_ECUC for f in model.findings)
    # the first mapping of an event is the one shown
    first = model.task_of(on_speed)
    assert first is not None and first.task == "Task_Event"
    conflict = findings_with(model, codes.ECUC_MAPPING_CONFLICT)[0]
    assert "Task_Event" in conflict.message and "Task_Other" in conflict.message
    assert len(model.task_mappings) == 3


def test_ecuc_containers_in_other_files_are_ignored(repo: Repo) -> None:
    """Only the configured ECUC file feeds tasks and mappings."""
    repo.write("Config/Developer/Stray_ecuc.arxml", samples.ecuc_doc())
    model = repo.load()
    assert model.tasks == frozenset() and model.task_mappings == {}
