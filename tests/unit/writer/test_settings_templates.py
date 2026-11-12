"""The [writer] table, value specifications and the order tables."""

from __future__ import annotations

from pathlib import Path

from porttool.core.config import ProjectConfig
from porttool.writer import WriterSettings, codes
from porttool.writer.order import ORDER, position
from porttool.writer.templates import number, value_spec
from porttool.writer.xmltext import Document, Node


def _settings(**table: object) -> tuple[WriterSettings, list[str]]:
    config = ProjectConfig(repo_root=Path("."), extras={"writer": table})
    settings, findings = WriterSettings.from_config(config)
    assert all(f.code == codes.SETTING for f in findings)
    return settings, [f.message for f in findings]


def test_defaults_without_a_writer_table() -> None:
    settings, messages = WriterSettings.from_config(ProjectConfig(repo_root=Path(".")))
    assert settings == WriterSettings() and not messages
    assert (settings.new_object_placement, settings.uuid) == ("existing_file", "auto")


def test_valid_settings_are_taken() -> None:
    settings, messages = _settings(
        new_object_placement="new_file",
        uuid="none",
        queue_length=4,
        client_com_spec=False,
        server_call_timeout="1.5",
        compu_method_pattern="{name}_CM",
        access_point_prefixes={"implicit read": "R"},
        data_type_mapping_set="/Maps/Set",
    )
    assert not messages
    assert settings.new_object_placement == "new_file" and settings.uuid == "none"
    assert settings.queue_length == 4 and settings.client_com_spec is False
    assert settings.compu_method_pattern == "{name}_CM"
    assert settings.access_point_prefixes["implicit read"] == "R"
    assert settings.access_point_prefixes["implicit write"] == "DWA"  # other defaults stay


def test_wrong_settings_give_a_warning_each_and_keep_the_default() -> None:
    settings, messages = _settings(
        new_object_placement="somewhere",
        uuid=5,
        queue_length="many",
        client_com_spec="no",
        receiver_com_spec_defaults=1,
        server_call_timeout=3,
        event_patterns="TMT",
        access_point_prefixes={"implicit read": 7},
        compu_method_pattern="{name}{other}",
        constraint_pattern="",
        unknown_key=True,
    )
    assert settings == WriterSettings()
    assert len(messages) == 11 and all(m.startswith("[writer] ") for m in messages)
    assert "[writer] unknown setting 'unknown_key' is ignored." in messages
    assert any("new_object_placement must be one of existing_file, new_file" in m for m in messages)
    assert any("queue_length must be a whole number" in m for m in messages)
    assert any("constraint_pattern may only use the placeholders name" in m for m in messages)


def _text(node: Node) -> str:
    document = Document("x", "<A>\n</A>\n")
    document.insert(document.root, node)
    return document.render()


def test_value_specifications_for_every_textual_form() -> None:
    constants = {"/Constants/C_Init"}
    text = _text(
        value_spec(
            "{1, [0x1F, -2.5e3], /Constants/C_Init, ON, /No/Constant}", constants.__contains__
        )
    )
    assert text == (
        "<A>\n"
        "  <RECORD-VALUE-SPECIFICATION>\n"
        "    <FIELDS>\n"
        "      <NUMERICAL-VALUE-SPECIFICATION>\n"
        "        <VALUE>1</VALUE>\n"
        "      </NUMERICAL-VALUE-SPECIFICATION>\n"
        "      <ARRAY-VALUE-SPECIFICATION>\n"
        "        <ELEMENTS>\n"
        "          <NUMERICAL-VALUE-SPECIFICATION>\n"
        "            <VALUE>0x1F</VALUE>\n"
        "          </NUMERICAL-VALUE-SPECIFICATION>\n"
        "          <NUMERICAL-VALUE-SPECIFICATION>\n"
        "            <VALUE>-2.5e3</VALUE>\n"
        "          </NUMERICAL-VALUE-SPECIFICATION>\n"
        "        </ELEMENTS>\n"
        "      </ARRAY-VALUE-SPECIFICATION>\n"
        "      <CONSTANT-REFERENCE>\n"
        '        <CONSTANT-REF DEST="CONSTANT-SPECIFICATION">/Constants/C_Init</CONSTANT-REF>\n'
        "      </CONSTANT-REFERENCE>\n"
        "      <TEXT-VALUE-SPECIFICATION>\n"
        "        <VALUE>ON</VALUE>\n"
        "      </TEXT-VALUE-SPECIFICATION>\n"
        "      <TEXT-VALUE-SPECIFICATION>\n"
        "        <VALUE>/No/Constant</VALUE>\n"
        "      </TEXT-VALUE-SPECIFICATION>\n"
        "    </FIELDS>\n"
        "  </RECORD-VALUE-SPECIFICATION>\n"
        "</A>\n"
    )
    assert "<ELEMENTS/>" in _text(value_spec("[]", constants.__contains__))
    assert "<VALUE>a &lt; b</VALUE>" in _text(value_spec(" a < b ", constants.__contains__))


def test_numbers_are_written_without_noise() -> None:
    assert [number(v) for v in (0, 1.0, -40.0, 0.5, 0.01, 1e-05)] == [
        "0",
        "1",
        "-40",
        "0.5",
        "0.01",
        "1e-05",
    ]


def test_position_of_a_new_container() -> None:
    runnable = ["SHORT-NAME", "MINIMUM-START-INTERVAL", "DATA-READ-ACCESSS", "SYMBOL"]
    assert position("RUNNABLE-ENTITY", "DATA-SEND-POINTS", runnable) == (3, True)
    assert position("RUNNABLE-ENTITY", "DATA-READ-ACCESSS", runnable[:2]) == (2, True)
    assert position("RUNNABLE-ENTITY", "WAIT-POINTS", runnable) == (4, True)
    # an unknown sibling: the index is a guess and the caller is told
    assert position("RUNNABLE-ENTITY", "DATA-SEND-POINTS", [*runnable, "X"]) == (3, False)
    # an element without a table: at the end, certain only if there is nothing to be wrong about
    assert position("UNKNOWN", "PORTS", []) == (0, True)
    assert position("UNKNOWN", "PORTS", ["SHORT-NAME"]) == (1, False)
    assert position("AR-PACKAGE", "ELEMENTS", ["SHORT-NAME", "AR-PACKAGES"]) == (1, True)


def test_order_tables_have_no_duplicates() -> None:
    for tag, table in ORDER.items():
        assert len(set(table)) == len(table), tag
