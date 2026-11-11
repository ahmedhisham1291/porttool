"""The tokenizer and the byte-identical round trip."""

from __future__ import annotations

from typing import Any

import pytest
from lxml import etree

from porttool.writer.xmltext import Document, Node, XmlIndexError, build_index, element, named
from tests.fixtures.generator import SMALL, render

FIXTURE_FILES = {
    name: data
    for name, data in render(SMALL)[0].items()
    if name.endswith((".arxml", ".dcf", ".dpa"))
}

TRICKY = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<!-- a comment with <PORTS> and </PORTS> inside -->\n"
    "<?pi some <instruction> ?>\n"
    '<AUTOSAR xmlns="http://autosar.org/schema/r4.0" note="a > b" other=\'x > "y"\'>\n'
    "  <AR-PACKAGES>\n"
    "    <AR-PACKAGE>\n"
    "      <SHORT-NAME>Pkg</SHORT-NAME>\n"
    "      <DESC><![CDATA[ </AR-PACKAGE> <ELEMENTS> ]]></DESC>\n"
    "      <ELEMENTS>\n"
    '        <UNIT UUID="1">\n'
    "          <SHORT-NAME>U</SHORT-NAME>\n"
    "          <DISPLAY-NAME/>\n"
    "          <!-- </UNIT> -->\n"
    "        </UNIT >\n"
    "        <EMPTY />\n"
    "      </ELEMENTS>\n"
    "    </AR-PACKAGE>\n"
    "  </AR-PACKAGES>\n"
    "</AUTOSAR>\n"
)


def _walk(node: Node) -> list[Node]:
    out = [node]
    for child in node.children:
        out.extend(_walk(child))
    return out


def _lxml_tags(root: Any) -> list[str]:
    return [etree.QName(el).localname for el in root.iter() if isinstance(el.tag, str)]


@pytest.mark.parametrize("name", sorted(FIXTURE_FILES))
def test_no_change_round_trip_is_byte_identical_for_every_fixture_file(name: str) -> None:
    data = FIXTURE_FILES[name]
    document = Document.from_bytes(name, data)
    assert not document.changed
    assert document.to_bytes() == data


@pytest.mark.parametrize("name", sorted(FIXTURE_FILES))
def test_index_has_the_same_elements_as_lxml(name: str) -> None:
    data = FIXTURE_FILES[name]
    document = Document.from_bytes(name, data)
    ours = [node.tag for node in _walk(document.root)]
    assert ours == _lxml_tags(etree.fromstring(data))


def _variants(data: bytes) -> dict[str, bytes]:
    text = data.decode("utf-8")
    crlf = text.replace("\n", "\r\n")
    mixed = "\n".join(
        line + ("\r" if index % 3 == 0 else "") for index, line in enumerate(text.split("\n"))
    )
    utf16 = text.replace('encoding="UTF-8"', 'encoding="UTF-16"')
    return {
        "crlf": crlf.encode("utf-8"),
        "mixed": mixed.encode("utf-8"),
        "tabs": text.replace("  ", "\t").encode("utf-8"),
        "bom": b"\xef\xbb\xbf" + data,
        "no-declaration": text.split("\n", 1)[1].encode("utf-8"),
        "utf-16-le": b"\xff\xfe" + utf16.encode("utf-16-le"),
        "utf-16-be": b"\xfe\xff" + utf16.encode("utf-16-be"),
        "latin-1": text.replace('encoding="UTF-8"', 'encoding="ISO-8859-1"').encode("latin-1"),
        "prefix": text.replace("<AUTOSAR xmlns=", "<AR:AUTOSAR xmlns:AR=")
        .replace("</", "</AR:")
        .replace("<", "<AR:")
        .replace("<AR:/AR:", "</AR:")
        .replace("<AR:?xml", "<?xml")
        .replace("<AR:AR:AUTOSAR", "<AR:AUTOSAR")
        .encode("utf-8"),
        "no-final-newline": data.rstrip(b"\n"),
    }


SWC_FILE = FIXTURE_FILES["Config/Developer/ComponentTypes/Swc1.arxml"]


@pytest.mark.parametrize("variant", sorted(_variants(SWC_FILE)))
def test_no_change_round_trip_is_byte_identical_for_encodings_and_styles(variant: str) -> None:
    data = _variants(SWC_FILE)[variant]
    document = Document.from_bytes("x.arxml", data)
    assert document.to_bytes() == data
    assert [node.tag for node in _walk(document.root)] == _lxml_tags(etree.fromstring(data))


def test_style_detection() -> None:
    variants = _variants(SWC_FILE)
    assert Document.from_bytes("x", SWC_FILE).eol == "\n"
    assert Document.from_bytes("x", SWC_FILE).unit == "  "
    assert Document.from_bytes("x", variants["crlf"]).eol == "\r\n"
    assert Document.from_bytes("x", variants["tabs"]).unit == "\t"
    assert Document.from_bytes("x", variants["prefix"]).prefix == "AR:"
    assert Document.from_bytes("x", variants["bom"]).bom == b"\xef\xbb\xbf"
    assert Document.from_bytes("x", variants["utf-16-be"]).codec == "utf-16-be"
    assert Document.from_bytes("x", variants["latin-1"]).codec == "iso8859-1"


def test_comments_cdata_processing_instructions_and_quoted_angle_brackets() -> None:
    document = Document("x", TRICKY)
    assert document.render() == TRICKY
    assert [node.tag for node in _walk(document.root)] == _lxml_tags(
        etree.fromstring(TRICKY.encode())
    )
    unit = document.find("/Pkg/U")
    assert unit is not None
    assert document.text[unit.start : unit.start_end] == '<UNIT UUID="1">'
    assert document.text[unit.end_start : unit.end] == "</UNIT >"
    assert document.attribute(unit, "UUID") == "1"
    assert document.attribute(document.root, "note") == "a > b"
    assert document.attribute(document.root, "other") == 'x > "y"'
    assert document.any_uuid() == "1"
    display = unit.child("DISPLAY-NAME")
    assert display is not None and display.end_start == -1 and not display.children


def test_offsets_point_at_the_tags() -> None:
    document = Document.from_bytes("x", SWC_FILE)
    for node in _walk(document.root):
        assert document.text.startswith("<" + node.raw, node.start)
        assert document.text[node.start_end - 1] == ">"
        if node.end_start >= 0:
            assert document.text.startswith("</" + node.raw, node.end_start)
            assert document.text[node.end - 1] == ">"


def test_doctype_with_internal_subset_is_skipped() -> None:
    text = '<!DOCTYPE AUTOSAR [ <!ENTITY a "<b>"> ]>\n<AUTOSAR><X/></AUTOSAR>'
    assert [node.tag for node in _walk(build_index(text))] == ["AUTOSAR", "X"]


@pytest.mark.parametrize(
    "text",
    [
        "<A><B></A></B>",
        "<A><B></B>",
        "<A></A></A>",
        "<A/><B/>",
        "no xml at all",
        "<A><!-- never closed </A>",
    ],
)
def test_broken_nesting_is_refused(text: str) -> None:
    with pytest.raises(XmlIndexError):
        build_index(text)


def test_unknown_or_ambiguous_encoding_is_refused() -> None:
    with pytest.raises(XmlIndexError):
        Document.from_bytes("x", b'<?xml version="1.0" encoding="KLINGON"?><A/>')
    with pytest.raises(XmlIndexError):
        Document.from_bytes("x", b'<?xml version="1.0" encoding="UTF-16"?><A/>')
    with pytest.raises(XmlIndexError):
        Document.from_bytes("x", b"<A>\xff\xff</A>")


def test_find_walks_short_name_paths() -> None:
    document = Document.from_bytes("x", SWC_FILE)
    swc = document.find("/ComponentTypes/Swc1")
    assert swc is not None and swc.tag.endswith("-SW-COMPONENT-TYPE")
    behavior = document.find("/ComponentTypes/Swc1/Swc1_InternalBehavior")
    assert behavior is not None and behavior.tag == "SWC-INTERNAL-BEHAVIOR"
    assert document.find("/ComponentTypes/Nope") is None
    assert document.find("/") is None
    names = [document.name_of(node) for node in document.identifiables(swc)]
    assert "Swc1_InternalBehavior" in names and len(names) > 2


def _insert_all_ways(text: str) -> str:
    document = Document("x", text)
    for tag in ("B", "C", "D", "E"):
        parent = document.root.child(tag)
        assert parent is not None
        document.insert(parent, named("NEW", "n&<>", element("V", text="1"), uuid='a"b'))
    first = document.root.children[0]
    document.insert(document.root, element("FIRST"), document.root.children.index(first))
    result = document.render()
    etree.fromstring(result.encode())
    document.reset()
    assert document.render() == text and not document.changed
    return result


def test_insertions_in_every_layout_of_the_container() -> None:
    text = (
        "<A>\n  <B>\n    <X/>\n    <!-- last -->\n  </B>\n  <C></C>\n  <D/>\n  <E><X/></E>\n</A>\n"
    )
    assert _insert_all_ways(text) == (
        "<A>\n"
        "  <FIRST/>\n"
        "  <B>\n"
        "    <X/>\n"
        "    <!-- last -->\n"
        '    <NEW UUID="a&quot;b">\n'
        "      <SHORT-NAME>n&amp;&lt;&gt;</SHORT-NAME>\n"
        "      <V>1</V>\n"
        "    </NEW>\n"
        "  </B>\n"
        "  <C>\n"
        '    <NEW UUID="a&quot;b">\n'
        "      <SHORT-NAME>n&amp;&lt;&gt;</SHORT-NAME>\n"
        "      <V>1</V>\n"
        "    </NEW>\n"
        "  </C>\n"
        "  <D>\n"
        '    <NEW UUID="a&quot;b">\n'
        "      <SHORT-NAME>n&amp;&lt;&gt;</SHORT-NAME>\n"
        "      <V>1</V>\n"
        "    </NEW>\n"
        "  </D>\n"
        "  <E><X/>\n"
        '    <NEW UUID="a&quot;b">\n'
        "      <SHORT-NAME>n&amp;&lt;&gt;</SHORT-NAME>\n"
        "      <V>1</V>\n"
        "    </NEW>\n"
        "  </E>\n"
        "</A>\n"
    )


def test_insertions_into_a_file_on_one_line_stay_well_formed() -> None:
    result = _insert_all_ways("<A><B><X/></B><C></C><D/><E><X/></E></A>")
    assert result.count("<NEW") == 4 and "<FIRST/>" in result


def test_new_elements_can_be_nested_and_found_before_rendering() -> None:
    document = Document("x", "<A>\n\t<P>\n\t\t<SHORT-NAME>p</SHORT-NAME>\n\t</P>\n</A>\n")
    package = document.find("/p")
    assert package is not None
    holder = element("ELEMENTS")
    document.insert(package, holder)
    document.insert(holder, named("UNIT", "u1"))
    document.insert(holder, named("UNIT", "u2"))
    found = document.find("/p/u2")
    assert found is not None and found.virtual
    assert document.render() == (
        "<A>\n\t<P>\n\t\t<SHORT-NAME>p</SHORT-NAME>\n"
        "\t\t<ELEMENTS>\n"
        "\t\t\t<UNIT>\n\t\t\t\t<SHORT-NAME>u1</SHORT-NAME>\n\t\t\t</UNIT>\n"
        "\t\t\t<UNIT>\n\t\t\t\t<SHORT-NAME>u2</SHORT-NAME>\n\t\t\t</UNIT>\n"
        "\t\t</ELEMENTS>\n"
        "\t</P>\n</A>\n"
    )
    assert document.render_element(package).startswith("<P>\n\t\t<SHORT-NAME>p</SHORT-NAME>\n")
    assert document.render_element(found) == "<UNIT>\n\t<SHORT-NAME>u2</SHORT-NAME>\n</UNIT>"
