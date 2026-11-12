"""Settings of the davinci package and the rendering of command templates."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from porttool.core.config import ProjectConfig
from porttool.core.results import Severity
from porttool.davinci.settings import (
    ACTIONS,
    BUILTIN_COMMANDS,
    DEFAULT_GUI_PROCESS_NAMES,
    DEFAULT_SCRIPT_DIR,
    blocking_findings,
    load_settings,
)
from porttool.davinci.templates import (
    ALL_PLACEHOLDERS,
    CommandTemplate,
    parse_template,
    placeholders_in,
    render_command,
    toml_list,
    toml_string,
)
from tests.unit.davinci.helpers import codes, make_config


def _config(tmp_path: Path, table: Mapping[str, object] | None = None) -> ProjectConfig:
    extras = {} if table is None else {"davinci": table}
    return ProjectConfig(repo_root=tmp_path, extras=MappingProxyType(extras))


def test_defaults_without_a_davinci_table(tmp_path: Path) -> None:
    settings, findings = load_settings(_config(tmp_path))
    assert findings == ()
    assert settings.unverified_actions() == ACTIONS
    assert {a: settings.commands[a].args for a in ACTIONS} == dict(BUILTIN_COMMANDS)
    assert settings.results.exit_codes_for("generate") == (0,)
    assert settings.encoding == "auto"
    assert settings.max_kept_output_lines == 2000
    assert settings.gui_process_names == DEFAULT_GUI_PROCESS_NAMES
    assert settings.script_dir == DEFAULT_SCRIPT_DIR
    assert settings.script_task == "PortToolMapEvents"
    assert settings.leftover_children == "kill"
    assert all(settings.executables[tool] == "" for tool in settings.executables)


def test_default_patterns_match_what_the_task_asks_for(tmp_path: Path) -> None:
    rules = load_settings(_config(tmp_path))[0].results
    assert rules.error_patterns[0].search("An ERROR occurred")
    assert rules.warning_patterns[0].search("Warning: something")
    for text in (
        "License not found",
        "The licence is missing",
        "Your license has expired",
        "license server unavailable",
        "Could not connect to the license server",
        "No valid license found",
        "License server unreachable",
    ):
        assert rules.license_patterns[0].search(text), text
    assert not rules.license_patterns[0].search("License check passed")
    assert not rules.license_patterns[0].search("file not found")


def test_owner_template_counts_as_verified(tmp_path: Path) -> None:
    table = {"commands": {"check": ["{exe}", "--workspace", "{dcf}"]}}
    settings, findings = load_settings(_config(tmp_path, table))
    assert findings == ()
    assert settings.commands["check"].verified
    assert settings.commands["check"].args == ("{exe}", "--workspace", "{dcf}")
    assert not settings.commands["generate"].verified
    assert "check" not in settings.unverified_actions()


def test_template_that_is_not_a_list(tmp_path: Path) -> None:
    table = {"commands": {"check": "{exe} -w {dcf}", "generate": {"a": 1}, "import": []}}
    settings, findings = load_settings(_config(tmp_path, table))
    assert codes(findings) == ["DVR-TPL-NOT-LIST", "DVR-TPL-EMPTY", "DVR-TPL-NOT-LIST"]
    assert all(finding.severity is Severity.ERROR for finding in findings)
    assert "must be a list of arguments" in findings[0].message
    assert not settings.commands["check"].usable
    # Only the broken action is blocked.
    assert codes(blocking_findings(findings, "check")) == ["DVR-TPL-NOT-LIST"]
    assert blocking_findings(findings, "validate") == ()


def test_template_with_a_number_or_an_unknown_placeholder(tmp_path: Path) -> None:
    table = {"commands": {"check": ["{exe}", 3], "validate": ["{exe}", "{workspace}"]}}
    settings, findings = load_settings(_config(tmp_path, table))
    assert codes(findings) == ["DVR-TPL-NOT-LIST", "DVR-TPL-UNKNOWN"]
    assert "{workspace}" in findings[1].message
    assert not settings.commands["validate"].usable


def test_wrong_types_unknown_keys_and_bad_values_are_findings(tmp_path: Path) -> None:
    table: dict[str, object] = {
        "encoding": "klingon",
        "max_kept_output_lines": 0,
        "gui_process_names": "DaVinciDEV.exe",
        "leftover_children": "ignore",
        "typo": 1,
        "executables": {"dvcfgcmd": 5, "other": "x"},
        "results": {
            "error_patterns": ["(unclosed"],
            "success_exit_codes": {"generate": [0, 2], "check": "0", "nonsense": [1]},
        },
    }
    settings, findings = load_settings(_config(tmp_path, table))
    by_path = {finding.object_path: finding.code for finding in findings}
    assert by_path == {
        "davinci.encoding": "DVR-CFG-ENCODING",
        "davinci.max_kept_output_lines": "DVR-CFG-TYPE",
        "davinci.gui_process_names": "DVR-CFG-TYPE",
        "davinci.leftover_children": "DVR-CFG-TYPE",
        "davinci.typo": "DVR-CFG-UNKNOWN",
        "davinci.executables.dvcfgcmd": "DVR-CFG-TYPE",
        "davinci.executables.other": "DVR-CFG-UNKNOWN",
        "davinci.results.error_patterns": "DVR-CFG-REGEX",
        "davinci.results.success_exit_codes.check": "DVR-CFG-TYPE",
        "davinci.results.success_exit_codes.nonsense": "DVR-CFG-UNKNOWN",
    }
    assert settings.encoding == "auto"
    assert settings.max_kept_output_lines == 2000
    assert settings.results.exit_codes_for("generate") == (0, 2)
    assert settings.results.exit_codes_for("check") == (0,)
    # A broken pattern blocks every action: the result could not be judged.
    assert "DVR-CFG-REGEX" in codes(blocking_findings(findings, "generate"))
    assert "DVR-CFG-UNKNOWN" not in codes(blocking_findings(findings, "generate"))


def test_davinci_table_that_is_not_a_table(tmp_path: Path) -> None:
    config = ProjectConfig(
        repo_root=tmp_path, extras=MappingProxyType({"davinci": {"commands": [1, 2]}})
    )
    settings, findings = load_settings(config)
    assert codes(findings) == ["DVR-CFG-TYPE"]
    assert settings.unverified_actions() == ACTIONS


def test_relative_paths_are_taken_from_the_repo_root(tmp_path: Path) -> None:
    table = {"script_dir": "tools/groovy", "log_dir": "out/logs", "env": {"A": "1"}}
    settings, findings = load_settings(_config(tmp_path, table))
    assert findings == ()
    assert settings.script_dir == tmp_path / "tools" / "groovy"
    assert settings.log_dir == tmp_path / "out" / "logs"
    assert dict(settings.env) == {"A": "1"}


def test_enabled_steps_come_from_the_pipeline_table(tmp_path: Path) -> None:
    config = make_config(tmp_path, pipeline={"davinci_steps": ["check", "generate"]})
    assert load_settings(config)[0].enabled_steps == ("check", "generate")


# ------------------------------------------------------------------ templates


def _values(**extra: object) -> dict[str, object]:
    values: dict[str, object] = dict.fromkeys(ALL_PLACEHOLDERS)
    values.update(exe="C:/Tools/DVCfgCmd.exe", dpa="C:/My Ecu/Proj.dpa", log_dir="C:/logs")
    values.update(extra)
    return values


def _render(args: list[str], **extra: object) -> tuple[tuple[str, ...], list[str]]:
    template, problems = parse_template("generate", args, verified=True)
    assert problems == ()
    rendered = render_command(template, _values(**extra))  # type: ignore[arg-type]
    return rendered.argv, codes(rendered.findings)


def test_scalar_placeholders_are_replaced_inside_an_argument() -> None:
    argv, problems = _render(["{exe}", "-p", "{dpa}", "--log={log_dir}/gen.txt"])
    assert problems == []
    assert argv == ("C:/Tools/DVCfgCmd.exe", "-p", "C:/My Ecu/Proj.dpa", "--log=C:/logs/gen.txt")


def test_list_placeholder_expands_to_several_arguments() -> None:
    argv, _ = _render(["{exe}", "-m", "{modules}"], modules=["Rte", "Os"])
    assert argv == ("C:/Tools/DVCfgCmd.exe", "-m", "Rte", "Os")
    argv, _ = _render(["{exe}", "--module={modules}"], modules=["Rte", "Os"])
    assert argv == ("C:/Tools/DVCfgCmd.exe", "--module=Rte", "--module=Os")
    argv, _ = _render(["{exe}", "-m", "{modules}", "-x"], modules=[])
    assert argv == ("C:/Tools/DVCfgCmd.exe", "-m", "-x")
    argv, _ = _render(["{exe}", "-m", "{modules_csv}"], modules_csv="Rte,Os")
    assert argv == ("C:/Tools/DVCfgCmd.exe", "-m", "Rte,Os")


def test_doubled_braces_are_literal_braces() -> None:
    argv, problems = _render(["{exe}", '{{"a": 1}}', "{ not a placeholder }"])
    assert problems == []
    assert argv[1:] == ('{"a": 1}', "{ not a placeholder }")


def test_unresolved_placeholder_gives_no_command() -> None:
    argv, problems = _render(["{exe}", "-w", "{dcf}", "-i", "{arxml}"])
    assert argv == ()
    assert problems == ["DVR-TPL-UNRESOLVED", "DVR-TPL-UNRESOLVED"]


def test_unresolved_placeholder_message_says_what_to_set() -> None:
    template, _ = parse_template("check", ["{exe}", "{dcf}"], verified=True)
    rendered = render_command(template, {"exe": "x.exe", "dcf": None})
    assert not rendered.ok
    assert "dcf_file in the [paths] table" in rendered.findings[0].message
    assert "not started" in rendered.findings[0].message


def test_unusable_and_unknown_templates_do_not_render() -> None:
    broken, _ = parse_template("check", "one text", verified=True)
    assert codes(render_command(broken, {}).findings) == ["DVR-TPL-INVALID"]
    raw = CommandTemplate("check", ("{exe}", "{nope}"), verified=True)
    assert codes(render_command(raw, {"exe": "x"}).findings) == ["DVR-TPL-UNKNOWN"]
    empty = CommandTemplate("check", ("{exe}",), verified=True)
    assert codes(render_command(empty, {"exe": ""}).findings) == ["DVR-TPL-EMPTY"]


def test_two_list_placeholders_in_one_argument_are_refused() -> None:
    template, problems = parse_template("generate", ["{exe}", "{modules}{swcs}"], verified=True)
    assert codes(problems) == ["DVR-TPL-LIST-MIX"]
    assert not template.usable


def test_placeholders_in_and_display() -> None:
    assert placeholders_in(["{exe}", "a{dcf}b{exe}", "{{x}}"]) == ("exe", "dcf")
    template, _ = parse_template("check", ["{exe}", "{dcf}"], verified=False)
    rendered = render_command(template, {"exe": "C:/a b/t.exe", "dcf": "w.dcf"})
    assert rendered.display() == '"C:/a b/t.exe" w.dcf'
    assert template.label == "UNVERIFIED built-in guess"


def test_toml_helpers_round_trip_through_a_toml_parser() -> None:
    items = ["{exe}", r"C:\Vector\My Tools\DVCfgCmd.exe", "it's", 'say "hi"', "tab\there", "Größe"]
    parsed = tomllib.loads(f"x = {toml_list(items)}")
    assert parsed["x"] == items
    assert toml_string(r"C:\a") == r"'C:\a'"
    assert re.fullmatch(r'".*"', toml_string("it's"))
