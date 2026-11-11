"""golden-diff: what exactly did a hand-made reference change do to the files?

Two commits of the owner's repo are compared. For every changed XML file the result
lists the elements that were added, removed or changed, with their tag path and
SHORT-NAME path. Added elements come with their exact source text: these are the
templates the writer has to reproduce. Byte-level facts (line endings, indentation,
declaration) are compared separately, because DaVinci may rewrite them on save.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from xml.parsers import expat
from xml.sax.saxutils import escape, quoteattr

from porttool.core.errors import GitError
from porttool.discovery.context import Context
from porttool.discovery.gitinfo import Git, error_text
from porttool.discovery.names import Names, text_shape
from porttool.discovery.sections import safe_schema_location, safe_start_tag
from porttool.discovery.xmlfeed import XmlProblem
from porttool.discovery.xmlstyle import StyleFacts, analyze_style, decode_xml

XML_EXTENSIONS: tuple[str, ...] = (".arxml", ".dcf", ".dpa", ".xml")
MAX_XML_BYTES = 200 * 1024 * 1024
MAX_XML_FILES = 500
MAX_ELEMENT_CHARS = 20_000
MAX_FILE_CHARS = 200_000
MAX_CHANGES_PER_FILE = 2_000
COMMENT_TAG = "#comment"

_STYLE_FIELDS: tuple[str, ...] = (
    "line_endings",
    "indent",
    "declaration",
    "bom",
    "declared_encoding",
    "trailing_newline",
    "root_start_tag",
    "attribute_quote",
)

#: ARXML elements whose text is a name or free text: never kept by --anonymize.
_NAME_LIKE_TAGS: frozenset[str] = frozenset(
    {
        "SHORT-NAME", "SYMBOL", "VALUE", "VT", "SD", "LABEL", "SHORT-LABEL", "TT", "DISPLAY-NAME",
        "L-1", "L-2", "L-4", "L-5", "L-10", "SW-CODE-PREFIX", "USED-CODE-GENERATOR", "SW-VERSION",
    }
)  # fmt: skip
#: ARXML attributes whose values are tool-defined keys or identifiers, kept in every mode.
_KEPT_ATTRIBUTES: frozenset[str] = frozenset({"DEST", "UUID", "GID", "BASE"})


class Node:
    """One element (or comment) with the byte range it occupies in the UTF-8 source."""

    __slots__ = (
        "tag", "attrs", "line", "start", "end", "text", "tail", "children", "short_name",
        "digest", "self_closing",
    )  # fmt: skip

    def __init__(self, tag: str, attrs: tuple[tuple[str, str], ...], line: int, start: int) -> None:
        self.tag = tag
        self.attrs = attrs
        self.line = line
        self.start = start
        self.end = start
        self.text = ""
        self.tail = ""
        self.children: list[Node] = []
        self.short_name: str | None = None
        self.digest = b""
        self.self_closing = False

    @property
    def elements(self) -> list[Node]:
        return [child for child in self.children if child.tag != COMMENT_TAG]


def _local(tag: str) -> str:
    return tag.rpartition(":")[2]


def _seal(node: Node) -> None:
    """Compute the content digest of a finished node (children are already sealed)."""
    digest = hashlib.blake2b(digest_size=16)
    digest.update(node.tag.encode("utf-8"))
    for key, value in sorted(node.attrs):
        digest.update(b"\x00a" + key.encode("utf-8") + b"\x00" + value.encode("utf-8"))
    if node.children:
        digest.update(b"\x00m" + node.text.strip().encode("utf-8"))
        for child in node.children:
            digest.update(b"\x00c" + child.digest + child.tail.strip().encode("utf-8"))
    else:
        digest.update(b"\x00t" + node.text.encode("utf-8"))
    node.digest = digest.digest()


def parse_tree(data: bytes) -> tuple[Node | None, XmlProblem | None]:
    """Parse UTF-8 XML into nodes that remember their byte range. Returns (root, problem)."""
    parser = expat.ParserCreate("utf-8")
    parser.buffer_text = True
    parser.ordered_attributes = True
    stack: list[Node] = []
    roots: list[Node] = []

    def start(name: str, attrs: list[str]) -> None:
        pairs = tuple(zip(attrs[::2], attrs[1::2], strict=True))
        node = Node(name, pairs, parser.CurrentLineNumber, parser.CurrentByteIndex)
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)

    def end(_name: str) -> None:
        node = stack.pop()
        index = parser.CurrentByteIndex
        if not node.children and not node.text and data[index - 2 : index] == b"/>":
            node.self_closing = True
            node.end = index
        else:
            close = data.find(b">", index)
            node.end = close + 1 if close >= 0 else len(data)
        for child in node.children:
            if _local(child.tag) == "SHORT-NAME":
                node.short_name = child.text.strip()
                break
        _seal(node)

    def text(value: str) -> None:
        if not stack:
            return
        top = stack[-1]
        if top.children:
            top.children[-1].tail += value
        else:
            top.text += value

    def comment(value: str) -> None:
        if not stack:
            return
        node = Node(COMMENT_TAG, (), parser.CurrentLineNumber, parser.CurrentByteIndex)
        node.text = value
        close = data.find(b"-->", node.start)
        node.end = close + 3 if close >= 0 else len(data)
        _seal(node)
        stack[-1].children.append(node)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    parser.CommentHandler = comment
    try:
        parser.Parse(data, True)
    except expat.ExpatError as error:
        message = expat.errors.messages[error.code] if error.code else "XML syntax error"
        return None, XmlProblem("malformed", message, error.lineno, error.offset)
    return (roots[0] if roots else None), None


# --------------------------------------------------------------------------- rendering


@dataclass(frozen=True, slots=True)
class RenderPolicy:
    names: Names
    arxml: bool  # True: AUTOSAR vocabulary, enumeration literals may stay readable


def _anonymous_text(policy: RenderPolicy, tag: str, text: str) -> str:
    if not text.strip():
        return text
    allow = policy.arxml and _local(tag) not in _NAME_LIKE_TAGS
    if _local(tag).endswith("DEFINITION-REF"):
        return policy.names.definition_ref(text.strip())
    return policy.names.value(text, allow_enum=allow)


def _anonymous_attribute(policy: RenderPolicy, key: str, value: str) -> str:
    if key.startswith("xmlns") or key.endswith("schemaLocation"):
        return safe_schema_location(policy.names, value) or ""
    if policy.arxml and key in _KEPT_ATTRIBUTES:
        return value
    return policy.names.value(value)


def render_anonymous(node: Node, policy: RenderPolicy) -> str:
    """Serialise a node with every name replaced by its token. Layout whitespace stays."""
    if node.tag == COMMENT_TAG:
        return f"<!-- comment removed by --anonymize: {len(node.text)} characters -->"
    attributes = "".join(
        f" {key}={quoteattr(_anonymous_attribute(policy, key, value))}"
        for key, value in node.attrs
    )
    if node.self_closing:
        return f"<{node.tag}{attributes}/>"
    if node.children:
        inner = [node.text if not node.text.strip() else policy.names.token(node.text.strip())]
        for child in node.children:
            inner.append(render_anonymous(child, policy))
            tail = child.tail
            inner.append(tail if not tail.strip() else policy.names.token(tail.strip()))
        body = "".join(inner)
    else:
        body = escape(_anonymous_text(policy, node.tag, node.text))
    return f"<{node.tag}{attributes}>{body}</{node.tag}>"


def _register_names(node: Node, names: Names) -> None:
    pending = [node]
    while pending:
        current = pending.pop()
        if current.short_name:
            names.name(current.short_name)
        pending.extend(current.children)


def _indent_before(data: bytes, start: int) -> tuple[int, str]:
    """Start of the line a node begins on, if only blanks precede it; and those blanks."""
    line_start = max(data.rfind(b"\n", 0, start), data.rfind(b"\r", 0, start)) + 1
    lead = data[line_start:start]
    if lead.strip(b" \t"):
        return start, ""
    return line_start, lead.decode("utf-8", errors="replace")


def _cut(text: str, limit: int) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


# --------------------------------------------------------------------------- matching


def _match_children(
    old: Node, new: Node
) -> tuple[list[tuple[Node, Node]], list[Node], list[Node], bool]:
    """Pair the children of two matched nodes: (pairs, added, removed, reordered)."""
    old_named: dict[tuple[str, str], Node] = {}
    old_pool: dict[str, list[Node]] = {}
    for child in old.children:
        key = (child.tag, child.short_name) if child.short_name is not None else None
        if key is not None and key not in old_named:
            old_named[key] = child
        else:
            old_pool.setdefault(child.tag, []).append(child)
    pairs: list[tuple[Node, Node]] = []
    added: list[Node] = []
    new_pool: dict[str, list[Node]] = {}
    seen_named: set[tuple[str, str]] = set()
    for child in new.children:
        key = (child.tag, child.short_name) if child.short_name is not None else None
        if key is not None and key not in seen_named:
            seen_named.add(key)
            partner = old_named.pop(key, None)
            if partner is None:
                added.append(child)
            else:
                pairs.append((partner, child))
        else:
            new_pool.setdefault(child.tag, []).append(child)
    removed: list[Node] = list(old_named.values())
    for tag in dict.fromkeys(list(old_pool) + list(new_pool)):
        olds = old_pool.get(tag, [])
        news = new_pool.get(tag, [])
        by_digest: dict[bytes, list[Node]] = {}
        for candidate in olds:
            by_digest.setdefault(candidate.digest, []).append(candidate)
        left_new: list[Node] = []
        matched: set[int] = set()
        for candidate in news:
            same = by_digest.get(candidate.digest)
            if same:
                partner = same.pop(0)
                matched.add(id(partner))
                pairs.append((partner, candidate))
            else:
                left_new.append(candidate)
        left_old = [candidate for candidate in olds if id(candidate) not in matched]
        for partner, candidate in zip(left_old, left_new, strict=False):
            pairs.append((partner, candidate))
        added.extend(left_new[len(left_old) :])
        removed.extend(left_old[len(left_new) :])
    old_position = {id(child): index for index, child in enumerate(old.children)}
    new_position = {id(child): index for index, child in enumerate(new.children)}
    in_new_order = sorted(pairs, key=lambda pair: new_position[id(pair[1])])
    sequence = [old_position[id(partner)] for partner, _ in in_new_order]
    reordered = any(a > b for a, b in zip(sequence, sequence[1:], strict=False))
    added.sort(key=lambda child: new_position[id(child)])
    removed.sort(key=lambda child: old_position[id(child)])
    return in_new_order, added, removed, reordered


def _sibling_order(parent: Node, tag: str) -> str:
    named = [child.short_name for child in parent.children if child.tag == tag and child.short_name]
    if len(named) < 2:
        return "cannot tell (fewer than two named siblings)"
    if named == sorted(named):
        return "sorted by SHORT-NAME"
    if [n.lower() for n in named] == sorted(n.lower() for n in named):
        return "sorted by SHORT-NAME ignoring case"
    return "not sorted"


class _Differ:
    def __init__(
        self,
        head_data: bytes,
        policy: RenderPolicy,
        max_chars: int,
    ) -> None:
        self.head_data = head_data
        self.policy = policy
        self.max_chars = max_chars
        self.changes: list[dict[str, Any]] = []
        self.counts = {"added": 0, "removed": 0, "changed": 0}

    def _record(self, record: dict[str, Any]) -> None:
        self.counts[record["change"]] += 1
        if len(self.changes) < MAX_CHANGES_PER_FILE:
            self.changes.append(record)

    def _paths(self, tags: Sequence[str], shorts: Sequence[str], node: Node) -> dict[str, Any]:
        names = self.policy.names
        chain = list(shorts) + ([node.short_name] if node.short_name else [])
        return {
            "tag": node.tag,
            "tag_path": "/" + "/".join([*tags, node.tag]),
            "short_name_path": "/" + "/".join(names.name(name) for name in chain) if chain else "",
            "line": node.line,
        }

    def xml_of(self, node: Node, limit: int) -> dict[str, Any]:
        """Source text of an added node (or its anonymized rendering) plus its indentation."""
        start, indent = _indent_before(self.head_data, node.start)
        if self.policy.names.anonymized:
            text = indent + render_anonymous(node, self.policy)
            exact = False
        else:
            text = self.head_data[start : node.end].decode("utf-8", errors="replace")
            exact = True
        shown, cut = _cut(text, limit)
        return {
            "xml": shown,
            "xml_is_exact_source_text": exact,
            "xml_characters": len(text),
            "xml_cut": cut,
            "indentation": indent.replace("\t", "\\t"),
        }

    def added(
        self, node: Node, parent: Node | None, tags: Sequence[str], shorts: Sequence[str]
    ) -> None:
        record: dict[str, Any] = {"change": "added", **self._paths(tags, shorts, node)}
        if parent is not None:
            siblings = parent.children
            index = next(i for i, child in enumerate(siblings) if child is node)
            record["position"] = {
                "index": index,
                "of": len(siblings),
                "after": self._sibling(siblings[index - 1]) if index > 0 else None,
                "before": self._sibling(siblings[index + 1])
                if index + 1 < len(siblings)
                else None,
                "siblings_with_this_tag": _sibling_order(parent, node.tag),
            }
        limit = self.max_chars if parent is not None else MAX_FILE_CHARS
        record.update(self.xml_of(node, limit))
        self._record(record)

    def _sibling(self, node: Node) -> dict[str, Any]:
        name = self.policy.names.name(node.short_name) if node.short_name else None
        return {"tag": node.tag, "short_name": name}

    def removed(self, node: Node, tags: Sequence[str], shorts: Sequence[str]) -> None:
        self._record({"change": "removed", **self._paths(tags, shorts, node)})

    def compare(self, old: Node, new: Node, tags: list[str], shorts: list[str]) -> None:
        if old.digest == new.digest:
            return
        own: dict[str, Any] = {}
        if sorted(old.attrs) != sorted(new.attrs):
            before, after = dict(old.attrs), dict(new.attrs)
            own["attributes"] = [
                {
                    "name": key,
                    "old_shape": text_shape(before[key]) if key in before else None,
                    "new_shape": text_shape(after[key]) if key in after else None,
                }
                for key in dict.fromkeys([*before, *after])
                if before.get(key) != after.get(key)
            ]
        leaf = not old.children and not new.children
        old_text = old.text if leaf else old.text.strip()
        new_text = new.text if leaf else new.text.strip()
        if old_text != new_text:
            own["text"] = {"old_shape": text_shape(old_text), "new_shape": text_shape(new_text)}
        pairs, added, removed, reordered = _match_children(old, new)
        if reordered:
            own["children_reordered"] = True
        if own:
            self._record({"change": "changed", **self._paths(tags, shorts, new), "what": own})
        inner_tags = [*tags, new.tag]
        inner_shorts = [*shorts, new.short_name] if new.short_name else shorts
        for node in added:
            self.added(node, new, inner_tags, inner_shorts)
        for node in removed:
            self.removed(node, inner_tags, [*shorts, old.short_name] if old.short_name else shorts)
        for partner, node in pairs:
            self.compare(partner, node, inner_tags, inner_shorts)


# --------------------------------------------------------------------------- one file


def _style_json(style: StyleFacts, names: Names) -> dict[str, Any]:
    out = dict(style.as_dict())
    out["root_start_tag"] = safe_start_tag(names, style.root_start_tag)
    out["schema_location"] = safe_schema_location(names, style.schema_location)
    if names.anonymized and style.namespace is not None:
        out["namespace"] = names.value(style.namespace)
    return out


def _normalize_newlines(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _load(data: bytes) -> tuple[bytes, Node | None, XmlProblem | None]:
    try:
        text, _ = decode_xml(data)
    except UnicodeDecodeError:
        return b"", None, XmlProblem("encoding", "bytes do not match the declared encoding")
    except LookupError:
        return b"", None, XmlProblem("encoding", "the declared encoding is not known")
    if not text.strip():
        return b"", None, XmlProblem("empty", "the file has no content")
    utf8 = text.encode("utf-8")
    root, problem = parse_tree(utf8)
    return utf8, root, problem


def diff_xml(
    path: str,
    base: bytes | None,
    head: bytes | None,
    names: Names,
    max_chars: int = MAX_ELEMENT_CHARS,
) -> dict[str, Any]:
    """Element-level and byte-level comparison of two versions of one XML file.

    ``base`` is None for an added file, ``head`` is None for a deleted file.
    """
    policy = RenderPolicy(names=names, arxml=path.lower().endswith(".arxml"))
    result: dict[str, Any] = {"bytes": {}, "problems": {}}
    base_style = analyze_style(base) if base is not None else None
    head_style = analyze_style(head) if head is not None else None
    result["bytes"]["base"] = _style_json(base_style, names) if base_style else None
    result["bytes"]["head"] = _style_json(head_style, names) if head_style else None
    changed_style: list[str] = []
    if base_style is not None and head_style is not None:
        changed_style = [
            name
            for name in _STYLE_FIELDS
            if getattr(base_style, name) != getattr(head_style, name)
        ]
    result["bytes"]["changed"] = changed_style

    base_data, base_root, base_problem = _load(base) if base is not None else (b"", None, None)
    head_data, head_root, head_problem = _load(head) if head is not None else (b"", None, None)
    for side, problem in (("base", base_problem), ("head", head_problem)):
        if problem is not None:
            result["problems"][side] = problem.describe()
    for root in (base_root, head_root):
        if root is not None and names.anonymized:
            _register_names(root, names)

    differ = _Differ(head_data, policy, max_chars)
    if head_root is not None and base_root is None and base is None:
        differ.added(head_root, None, [], [])
    elif base_root is not None and head_root is None and head is None:
        differ.removed(base_root, [], [])
    elif base_root is not None and head_root is not None:
        if base_root.tag != head_root.tag:
            differ.removed(base_root, [], [])
            differ.added(head_root, None, [], [])
        else:
            differ.compare(base_root, head_root, [], [])
    compared = base_root is not None and head_root is not None
    same_content = compared and differ.counts == {"added": 0, "removed": 0, "changed": 0}
    same_bytes = base == head
    only_newlines = (
        base is not None
        and head is not None
        and not same_bytes
        and _normalize_newlines(base) == _normalize_newlines(head)
    )
    result["elements"] = differ.changes
    result["summary"] = {
        "elements_added": differ.counts["added"],
        "elements_removed": differ.counts["removed"],
        "elements_changed": differ.counts["changed"],
        "listed": len(differ.changes),
        "element_content_identical": same_content,
        "only_line_endings_changed": only_newlines,
        "only_layout_changed": same_content and not same_bytes and not only_newlines,
    }
    return result


# --------------------------------------------------------------------------- command


@dataclass(slots=True)
class GoldenOptions:
    base: str
    head: str
    max_xml_bytes: int = MAX_XML_BYTES
    max_xml_files: int = MAX_XML_FILES
    max_element_chars: int = MAX_ELEMENT_CHARS


def resolve_commit(git: Git, text: str) -> str:
    """Full hash of a commit the user named. Raises GitError with a clear message."""
    if not text or text.startswith("-"):
        raise GitError(f"'{text}' is not a commit.")
    result = git.run("rev-parse", "--verify", "--quiet", f"{text}^{{commit}}")
    if result.spawn_error is not None:
        raise GitError(
            "git could not be started. Install Git for Windows or pass --git <path to git.exe>."
        )
    if not result.ok:
        probe = git.run("rev-parse", "--is-inside-work-tree")
        if not probe.ok:
            raise GitError("The folder given with --repo is not a git work tree.")
        raise GitError(f"git does not know the commit '{text}'.")
    return result.stdout.decode("ascii", errors="replace").strip()


def changed_files(git: Git, base: str, head: str) -> list[tuple[str, str]]:
    """(status, path) of every file that differs between two commits. Renames are A + D."""
    result = git.run("diff", "--name-status", "-z", "--no-renames", base, head)
    if not result.ok:
        raise GitError(f"git diff failed ({error_text(result)}).")
    fields = result.stdout.decode("utf-8", errors="replace").split("\0")
    pairs = [(fields[i], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]
    return [(status, path) for status, path in pairs if status and path]


_STATUS_NAMES = {"A": "added", "D": "deleted", "M": "modified", "T": "type changed"}


def build_golden_diff(git: Git, options: GoldenOptions, ctx: Context) -> dict[str, Any]:
    """Compare two commits. Problems of single files become findings; git errors raise."""
    names = ctx.names
    base = resolve_commit(git, options.base)
    head = resolve_commit(git, options.head)
    ctx.progress(f"  comparing {base[:10]} (base) with {head[:10]} (head)")
    listed = changed_files(git, base, head)
    files: list[dict[str, Any]] = []
    totals = {
        "files_changed": len(listed),
        "added": 0,
        "deleted": 0,
        "modified": 0,
        "xml_files_compared": 0,
        "elements_added": 0,
        "elements_removed": 0,
        "elements_changed": 0,
        "files_with_only_line_ending_changes": 0,
        "files_with_only_layout_changes": 0,
    }
    compared = 0
    for status, path in listed:
        status_name = _STATUS_NAMES.get(status[:1], status)
        if status_name in totals:
            totals[status_name] += 1
        is_xml = path.lower().endswith(XML_EXTENSIONS)
        record: dict[str, Any] = {
            "path": names.rel_path(path),
            "status": status_name,
            "xml": is_xml,
        }
        files.append(record)
        if not is_xml:
            continue
        if compared >= options.max_xml_files:
            record["skipped"] = f"more than {options.max_xml_files} XML files changed"
            continue
        compared += 1
        ctx.progress(f"  {status_name}: XML file {compared}")
        blobs: dict[str, bytes | None] = {"base": None, "head": None}
        failed = False
        for side, commit in (("base", base), ("head", head)):
            if (side == "base" and status_name == "added") or (
                side == "head" and status_name == "deleted"
            ):
                continue
            shown = git.show(commit, path, options.max_xml_bytes)
            if not shown.ok or shown.truncated:
                reason = (
                    f"larger than {options.max_xml_bytes} bytes"
                    if shown.truncated
                    else names.external(error_text(shown))
                )
                ctx.warning(
                    "DISC-GOLDEN-READ",
                    f"The {side} version of a changed file could not be read ({reason}).",
                    file=path,
                )
                record["skipped"] = f"{side} version could not be read"
                failed = True
                break
            blobs[side] = shown.stdout
        if failed:
            continue
        for side in ("base", "head"):
            blob = blobs[side]
            record[f"{side}_bytes"] = len(blob) if blob is not None else None
            if blob is not None and blob.startswith(b"version https://git-lfs.github.com/spec/"):
                ctx.warning(
                    "DISC-GOLDEN-LFS",
                    "A changed file is stored with Git LFS; only its pointer was compared.",
                    file=path,
                )
        try:
            detail = diff_xml(path, blobs["base"], blobs["head"], names, options.max_element_chars)
        except RecursionError:
            ctx.warning(
                "DISC-GOLDEN-DEPTH",
                "A changed file is nested too deeply to compare element by element.",
                file=path,
            )
            record["skipped"] = "nested too deeply"
            continue
        record.update(detail)
        for side, text in detail["problems"].items():
            ctx.warning(
                "DISC-GOLDEN-XML",
                f"The {side} version of a changed file is not well-formed XML ({text}).",
                file=path,
            )
        summary = detail["summary"]
        totals["xml_files_compared"] += 1
        totals["elements_added"] += summary["elements_added"]
        totals["elements_removed"] += summary["elements_removed"]
        totals["elements_changed"] += summary["elements_changed"]
        totals["files_with_only_line_ending_changes"] += summary["only_line_endings_changed"]
        totals["files_with_only_layout_changes"] += summary["only_layout_changed"]
    config = {
        key: git.text("config", "--get", key)
        for key in ("core.autocrlf", "core.eol", "core.safecrlf")
    }
    return {
        "base_commit": base,
        "head_commit": head,
        "git_config": config,
        "note": (
            "File content is read with 'git show <commit>:<path>': it is the content as "
            "committed. With core.autocrlf=true the files in the work tree have CRLF line "
            "endings even when the committed content has LF."
        ),
        "summary": totals,
        "files": files,
    }
