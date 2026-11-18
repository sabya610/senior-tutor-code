"""Language-aware parsing: symbols, relations and retrieval chunks with line numbers.

Everything emitted here is derived from the actual source text, so every symbol,
relation and chunk can be traced back to ``path:line``.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field

MAX_CHUNKS_PER_FILE = 400
MAX_SIGNATURE = 200


@dataclass
class Symbol:
    name: str
    kind: str
    start_line: int
    end_line: int
    signature: str = ""
    parent: str | None = None


@dataclass
class Relation:
    kind: str
    target: str
    line: int


@dataclass
class Chunk:
    start_line: int
    end_line: int
    kind: str  # code | doc | config
    symbol: str | None
    text: str


@dataclass
class ParsedFile:
    language: str
    line_count: int
    symbols: list[Symbol] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers

Segment = tuple[int, int, str, str | None]  # start, end (1-based inclusive), kind, symbol


def _sig(line: str) -> str:
    return line.strip()[:MAX_SIGNATURE]


def _windows(start: int, end: int, size: int, overlap: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    pos = start
    step = max(1, size - overlap)
    while pos <= end:
        stop = min(end, pos + size - 1)
        out.append((pos, stop))
        if stop >= end:
            break
        pos += step
    return out


def make_chunks(lines: list[str], segments: list[Segment], size: int, overlap: int) -> list[Chunk]:
    """Turn segments into chunks; oversize segments are split into overlapping windows."""
    chunks: list[Chunk] = []
    for start, end, kind, symbol in sorted(segments, key=lambda s: (s[0], s[1])):
        start, end = max(1, start), min(len(lines), end)
        if start > end:
            continue
        spans = [(start, end)] if end - start + 1 <= size * 2 else _windows(start, end, size, overlap)
        for s, e in spans:
            text = "\n".join(lines[s - 1 : e])
            if not text.strip():
                continue
            chunks.append(Chunk(s, e, kind, symbol, text))
            if len(chunks) >= MAX_CHUNKS_PER_FILE:
                return chunks
    return chunks


def fill_gaps(total: int, segments: list[Segment], kind: str, label: str | None = None) -> list[Segment]:
    """Add segments for any lines not covered by *segments* (module headers, constants...)."""
    out = list(segments)
    covered = sorted((s[0], s[1]) for s in segments)
    cursor = 1
    for s, e in covered:
        if s > cursor:
            out.append((cursor, s - 1, kind, label))
        cursor = max(cursor, e + 1)
    if cursor <= total:
        out.append((cursor, total, kind, label))
    return out


def block_segments(lines: list[str], boundary: re.Pattern[str], kind: str, min_lines: int = 12) -> tuple[list[Segment], list[tuple[int, str]]]:
    """Split on boundary lines; tiny blocks are merged forward so chunks stay meaningful."""
    marks: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        m = boundary.match(line)
        if m:
            marks.append((idx, (m.group(1) if m.groups() else line).strip()))
    if not marks:
        return [(1, len(lines), kind, None)], marks
    segments: list[Segment] = []
    if marks[0][0] > 1:
        segments.append((1, marks[0][0] - 1, kind, None))
    cur_start, cur_name = marks[0]
    for i, (line_no, name) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(lines) + 1
        if nxt - cur_start >= min_lines or i + 1 == len(marks):
            segments.append((cur_start, nxt - 1, kind, cur_name))
            if i + 1 < len(marks):
                cur_start, cur_name = marks[i + 1]
    return segments, marks


def find_block_end(lines: list[str], start_idx: int, max_lines: int = 3000) -> int:
    """Return the 0-based index of the line closing the first ``{`` block at/after start_idx."""
    depth, seen = 0, False
    in_raw = in_comment = False
    limit = min(len(lines), start_idx + max_lines)
    for i in range(start_idx, limit):
        line = lines[i]
        j, n = 0, len(line)
        while j < n:
            c = line[j]
            if in_comment:
                if line.startswith("*/", j):
                    in_comment = False
                    j += 2
                else:
                    j += 1
                continue
            if in_raw:
                if c == "`":
                    in_raw = False
                j += 1
                continue
            if line.startswith("//", j):
                break
            if line.startswith("/*", j):
                in_comment = True
                j += 2
                continue
            if c == "`":
                in_raw = True
            elif c in "\"'":
                k = j + 1
                while k < n and line[k] != c:
                    k += 2 if line[k] == "\\" else 1
                j = k + 1
                continue
            elif c == "{":
                depth += 1
                seen = True
            elif c == "}":
                depth -= 1
                if seen and depth <= 0:
                    return i
            j += 1
    return start_idx if not seen else limit - 1


# --------------------------------------------------------------------------- Python

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "websocket", "route"}
APP_FACTORIES = {"FastAPI", "Flask", "Starlette", "Quart", "Sanic"}


def _py_decorator_info(node: ast.AST) -> tuple[list[tuple[str, int]], bool]:
    routes: list[tuple[str, int]] = []
    reconciler = False
    for dec in getattr(node, "decorator_list", []):
        call = dec if isinstance(dec, ast.Call) else None
        target = call.func if call else dec
        if isinstance(target, ast.Attribute):
            chain = ast.unparse(target)
            if chain.startswith("kopf.on"):
                reconciler = True
            if call and target.attr in HTTP_METHODS and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                method = target.attr.upper()
                if method == "ROUTE":
                    method = "ANY"
                    for kw in call.keywords:
                        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                            method = "/".join(str(getattr(e, "value", "?")) for e in kw.value.elts)
                routes.append((f"{method} {call.args[0].value}", dec.lineno))
    return routes, reconciler


def _py_signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){ret}"[:MAX_SIGNATURE]


def parse_python(text: str, lines: list[str], size: int, overlap: int) -> ParsedFile:
    parsed = ParsedFile("python", len(lines))
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        parsed.chunks = make_chunks(lines, [(1, len(lines), "code", None)], size, overlap)
        return parsed

    segments: list[Segment] = []

    def start_of(node: ast.AST) -> int:
        return min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno])

    def add_function(node, parent: str | None) -> None:
        kind = "method" if parent else "function"
        name = f"{parent}.{node.name}" if parent else node.name
        start, end = start_of(node), node.end_lineno or node.lineno
        routes, reconciler = _py_decorator_info(node)
        parsed.symbols.append(Symbol(name, kind, start, end, _py_signature(node), parent))
        for route, line in routes:
            parsed.symbols.append(Symbol(route, "route", line, end, _py_signature(node), parent))
        if reconciler:
            parsed.symbols.append(Symbol(name, "reconciler", start, end, _py_signature(node), parent))
        segments.append((start, end, "code", name))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add_function(node, None)
        elif isinstance(node, ast.ClassDef):
            start, end = start_of(node), node.end_lineno or node.lineno
            parsed.symbols.append(Symbol(node.name, "class", start, end, _py_signature(node)))
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            for m in methods:
                add_function(m, node.name)
            if end - start + 1 <= size * 2 or not methods:
                # keep small classes whole: drop per-method segments added above
                segments[:] = [s for s in segments if not (s[3] or "").startswith(f"{node.name}.")]
                segments.append((start, end, "code", node.name))
            else:
                first = min(start_of(m) for m in methods)
                if first > start:
                    segments.append((start, first - 1, "code", node.name))
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            fname = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if fname in APP_FACTORIES and node.targets and isinstance(node.targets[0], ast.Name):
                parsed.symbols.append(Symbol(f"{fname} app ({node.targets[0].id})", "entrypoint", node.lineno, node.end_lineno or node.lineno, _sig(lines[node.lineno - 1])))
        elif isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__":
            parsed.symbols.append(Symbol("__main__", "entrypoint", node.lineno, node.end_lineno or node.lineno, _sig(lines[node.lineno - 1])))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parsed.relations.append(Relation("import", alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            parsed.relations.append(Relation("import", module, node.lineno))
            for alias in node.names:
                if alias.name != "*":
                    joiner = "" if module.endswith(".") else "."
                    parsed.relations.append(Relation("import_name", f"{module}{joiner}{alias.name}", node.lineno))

    segments = fill_gaps(len(lines), segments, "code", "module")
    parsed.chunks = make_chunks(lines, segments, size, overlap)
    return parsed


# --------------------------------------------------------------------------- Go

GO_PKG = re.compile(r"^package\s+(\w+)")
GO_FUNC = re.compile(r"^func\s+(?:\(\s*(?:\w+\s+)?\*?(\w+)(?:\[[^\]]*\])?\s*\)\s*)?(\w+)\s*(?:\[[^\]]*\])?\(")
GO_TYPE = re.compile(r"^type\s+(\w+)(?:\[[^\]]*\])?\s+(struct|interface)?")
GO_IMPORT_ONE = re.compile(r'^import\s+(?:[\w.]+\s+)?"([^"]+)"')
GO_IMPORT_ITEM = re.compile(r'^\s*(?:[\w.]+\s+)?"([^"]+)"')
GO_ROUTE_METHOD = re.compile(r'\.(GET|POST|PUT|DELETE|PATCH)\(\s*"([^"]+)"')
GO_ROUTE_HANDLE = re.compile(r'\b(?:HandleFunc|Handle)\(\s*"([^"]+)"')
GO_CONTROLLER_WATCH = re.compile(r"\.(For|Owns|Watches)\(\s*&?([\w.]+)")
GO_COBRA_USE = re.compile(r'\bUse:\s*"([^"]+)"')
GO_WEBHOOK_METHODS = {"ValidateCreate", "ValidateUpdate", "ValidateDelete", "Default", "Handle"}


def parse_go(text: str, lines: list[str], size: int, overlap: int) -> ParsedFile:
    parsed = ParsedFile("go", len(lines))
    package = ""
    in_import = False
    segments: list[Segment] = []
    is_webhook_file = "admission" in text or "webhook" in text.lower()
    is_cobra = "cobra.Command" in text

    for idx, line in enumerate(lines):
        no = idx + 1
        if in_import:
            if line.strip().startswith(")"):
                in_import = False
            else:
                m = GO_IMPORT_ITEM.match(line)
                if m:
                    parsed.relations.append(Relation("import", m.group(1), no))
            continue
        m = GO_PKG.match(line)
        if m:
            package = m.group(1)
            continue
        if line.startswith("import ("):
            in_import = True
            continue
        m = GO_IMPORT_ONE.match(line)
        if m:
            parsed.relations.append(Relation("import", m.group(1), no))
            continue
        m = GO_FUNC.match(line)
        if m:
            recv, name = m.group(1), m.group(2)
            end = find_block_end(lines, idx) + 1
            full = f"{recv}.{name}" if recv else name
            kind = "method" if recv else "function"
            parsed.symbols.append(Symbol(full, kind, no, end, _sig(line), recv))
            if name == "Reconcile" and "Request" in line:
                parsed.symbols.append(Symbol(full, "reconciler", no, end, _sig(line), recv))
            if is_webhook_file and recv and name in GO_WEBHOOK_METHODS:
                parsed.symbols.append(Symbol(full, "webhook", no, end, _sig(line), recv))
            if name == "main" and package == "main" and not recv:
                parsed.symbols.append(Symbol("main", "entrypoint", no, end, _sig(line)))
            segments.append((no, end, "code", full))
            continue
        m = GO_TYPE.match(line)
        if m and not line.startswith("type ("):
            kind = {"struct": "struct", "interface": "interface"}.get(m.group(2) or "", "type")
            end = find_block_end(lines, idx) + 1 if "{" in line else no
            parsed.symbols.append(Symbol(m.group(1), kind, no, end, _sig(line)))
            segments.append((no, end, "code", m.group(1)))
            continue
        for rm in GO_ROUTE_METHOD.finditer(line):
            parsed.symbols.append(Symbol(f"{rm.group(1)} {rm.group(2)}", "route", no, no, _sig(line)))
        rm = GO_ROUTE_HANDLE.search(line)
        if rm:
            parsed.symbols.append(Symbol(f"ANY {rm.group(1)}", "route", no, no, _sig(line)))
        wm = GO_CONTROLLER_WATCH.search(line)
        if wm and "Controller" in text:
            parsed.relations.append(Relation(f"controller_{wm.group(1).lower()}", wm.group(2), no))
        if is_cobra:
            cm = GO_COBRA_USE.search(line)
            if cm:
                parsed.symbols.append(Symbol(cm.group(1).split()[0], "command", no, no, _sig(line)))

    parsed.chunks = make_chunks(lines, fill_gaps(len(lines), segments, "code", "module"), size, overlap)
    return parsed


# --------------------------------------------------------------------------- Java

JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;")
JAVA_TYPE = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:(?:public|protected|private|abstract|final|static|sealed)\s+)*(class|interface|enum|record)\s+(\w+)")
JAVA_METHOD = re.compile(r"^\s+(?:@\w+(?:\([^)]*\))?\s*)*(?:(?:public|protected|private|static|final|abstract|synchronized|default)\s+)+[\w<>\[\],.? ]+?\s+(\w+)\s*\([^;{]*\)\s*(?:throws [\w., ]+)?\s*\{")
JAVA_ANNOTATION = re.compile(r"^\s*@(RestController|Controller|Service|Repository|Component|Configuration|SpringBootApplication|Entity)\b")
JAVA_MAPPING = re.compile(r'@(Get|Post|Put|Delete|Patch|Request)Mapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?\{?"([^"]*)"')


def parse_java(text: str, lines: list[str], size: int, overlap: int) -> ParsedFile:
    parsed = ParsedFile("java", len(lines))
    segments: list[Segment] = []
    pending_annotation: str | None = None
    current_class: str | None = None
    for idx, line in enumerate(lines):
        no = idx + 1
        m = JAVA_IMPORT.match(line)
        if m:
            parsed.relations.append(Relation("import", m.group(1), no))
            continue
        am = JAVA_ANNOTATION.match(line)
        if am:
            pending_annotation = am.group(1)
        rm = JAVA_MAPPING.search(line)
        if rm:
            verb = rm.group(1).upper() if rm.group(1) != "Request" else "ANY"
            parsed.symbols.append(Symbol(f"{verb} {rm.group(2)}", "route", no, no, _sig(line)))
        m = JAVA_TYPE.match(line)
        if m:
            end = find_block_end(lines, idx) + 1
            current_class = m.group(2)
            sig = _sig(line) + (f"  [@{pending_annotation}]" if pending_annotation else "")
            parsed.symbols.append(Symbol(current_class, m.group(1), no, end, sig))
            if pending_annotation == "SpringBootApplication":
                parsed.symbols.append(Symbol(current_class, "entrypoint", no, end, sig))
            pending_annotation = None
            if end - no + 1 <= size * 2:
                segments.append((no, end, "code", current_class))
            continue
        m = JAVA_METHOD.match(line)
        if m:
            end = find_block_end(lines, idx) + 1
            name = f"{current_class}.{m.group(1)}" if current_class else m.group(1)
            parsed.symbols.append(Symbol(name, "method", no, end, _sig(line), current_class))
            if m.group(1) == "main" and "static" in line:
                parsed.symbols.append(Symbol(name, "entrypoint", no, end, _sig(line), current_class))
            segments.append((no, end, "code", name))

    # Keep the finest-grained segments only: drop a class segment that contains method segments.
    segments = [s for s in segments if not any(o is not s and o[0] >= s[0] and o[1] <= s[1] and (o[0], o[1]) != (s[0], s[1]) for o in segments)]
    parsed.chunks = make_chunks(lines, fill_gaps(len(lines), segments, "code", None), size, overlap)
    return parsed


# --------------------------------------------------------------------------- TypeScript / JavaScript

JS_IMPORT = re.compile(r"""^\s*import\s+(?:.+?\s+from\s+)?['"]([^'"]+)['"]""")
JS_REQUIRE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")
JS_FUNC = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+(\w+)")
JS_CLASS = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)")
JS_ARROW = re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*(?::[^=]+)?=>")
JS_IFACE = re.compile(r"^\s*(?:export\s+)?(?:interface|type)\s+(\w+)")
JS_ROUTE = re.compile(r"""\b(?:app|router|server)\.(get|post|put|delete|patch)\(\s*['"`]([^'"`]+)""")


def parse_js(language: str, text: str, lines: list[str], size: int, overlap: int) -> ParsedFile:
    parsed = ParsedFile(language, len(lines))
    segments: list[Segment] = []
    for idx, line in enumerate(lines):
        no = idx + 1
        for rx in (JS_IMPORT, JS_REQUIRE):
            m = rx.search(line)
            if m:
                parsed.relations.append(Relation("import", m.group(1), no))
        rm = JS_ROUTE.search(line)
        if rm:
            parsed.symbols.append(Symbol(f"{rm.group(1).upper()} {rm.group(2)}", "route", no, no, _sig(line)))
        for rx, kind in ((JS_CLASS, "class"), (JS_FUNC, "function"), (JS_ARROW, "function"), (JS_IFACE, "interface")):
            m = rx.match(line)
            if m and not line.startswith((" " * 4, "\t\t")):
                end = find_block_end(lines, idx) + 1 if "{" in line else no
                parsed.symbols.append(Symbol(m.group(1), kind, no, max(end, no), _sig(line)))
                segments.append((no, max(end, no), "code", m.group(1)))
                break
    parsed.chunks = make_chunks(lines, fill_gaps(len(lines), segments, "code", "module"), size, overlap)
    return parsed


# --------------------------------------------------------------------------- YAML / Helm

YAML_DOC_SEP = re.compile(r"^---\s*$")
YAML_KIND = re.compile(r"^kind:\s*[\"']?([A-Za-z][\w.]*)")
YAML_META_NAME = re.compile(r"^\s{2}name:\s*[\"']?(.+?)[\"']?\s*$")
YAML_TOP_KEY = re.compile(r"^([A-Za-z0-9_.\"-]+):(?:\s|$)")
YAML_CRD_KIND = re.compile(r"^\s{4}kind:\s*[\"']?(\w+)")
HELM_DEFINE = re.compile(r"""^\s*\{\{-?\s*define\s+["']([^"']+)["']""")
YAML_IMAGE = re.compile(r"""^\s*-?\s*image:\s*["']?([^\s"'#]+)""")
YAML_SA = re.compile(r"""^\s*serviceAccountName:\s*["']?([^\s"'#]+)""")
YAML_NAMED = {
    "secretName": "secret",
    "claimName": "pvc",
    "serviceName": "service",
}
YAML_NAMED_RE = re.compile(r"""^\s*(secretName|claimName|serviceName):\s*["']?([^\s"'#]+)""")
YAML_REF_PARENT = re.compile(r"^(\s*)-?\s*(configMapKeyRef|configMapRef|configMap|secretKeyRef|secretRef|secret|service|persistentVolumeClaim|scaleTargetRef|roleRef):\s*$")
YAML_REF_KIND = {
    "configMapKeyRef": "configmap", "configMapRef": "configmap", "configMap": "configmap",
    "secretKeyRef": "secret", "secretRef": "secret", "secret": "secret",
    "service": "service", "persistentVolumeClaim": "pvc", "scaleTargetRef": "scale_target", "roleRef": "role",
}
YAML_REF_NAME = re.compile(r"""^\s*(?:name|claimName):\s*["']?([^\s"'#]+)""")
WEBHOOK_KINDS = {"MutatingWebhookConfiguration", "ValidatingWebhookConfiguration", "ValidatingAdmissionPolicy"}


def _yaml_relations(lines: list[str], parsed: ParsedFile) -> None:
    pending: tuple[int, str] | None = None  # (indent, kind)
    pending_age = 0
    for idx, line in enumerate(lines):
        no = idx + 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = YAML_IMAGE.match(line)
        if m and "{{" not in m.group(1):
            parsed.relations.append(Relation("image", m.group(1), no))
        m = YAML_SA.match(line)
        if m:
            parsed.relations.append(Relation("serviceaccount", m.group(1), no))
        m = YAML_NAMED_RE.match(line)
        if m:
            parsed.relations.append(Relation(YAML_NAMED[m.group(1)], m.group(2), no))
        indent = len(line) - len(line.lstrip())
        if pending:
            pending_age += 1
            m = YAML_REF_NAME.match(line)
            if m and indent > pending[0]:
                parsed.relations.append(Relation(pending[1], m.group(1), no))
                pending = None
            elif indent <= pending[0] or pending_age > 4:
                pending = None
        m = YAML_REF_PARENT.match(line)
        if m:
            pending, pending_age = (len(m.group(1)), YAML_REF_KIND[m.group(2)]), 0


def parse_yaml(rel_path: str, language: str, text: str, lines: list[str], size: int, overlap: int) -> ParsedFile:
    parsed = ParsedFile(language, len(lines))
    base = rel_path.rsplit("/", 1)[-1]
    _yaml_relations(lines, parsed)

    if base == "Chart.yaml":
        name = next((m.group(1) for l in lines if (m := re.match(r"^name:\s*[\"']?([^\s\"']+)", l))), None)
        version = next((m.group(1) for l in lines if (m := re.match(r"^version:\s*[\"']?([^\s\"']+)", l))), "")
        if name:
            parsed.symbols.append(Symbol(name, "helm_chart", 1, len(lines), f"chart {name} {version}".strip()))
        in_deps = False
        for no, l in enumerate(lines, 1):
            if l.startswith("dependencies:"):
                in_deps = True
            elif in_deps and (m := re.match(r"^\s*-\s*name:\s*[\"']?([^\s\"']+)", l)):
                parsed.relations.append(Relation("helm_dep", m.group(1), no))
            elif in_deps and l and not l.startswith((" ", "-", "#")):
                in_deps = False
        parsed.chunks = make_chunks(lines, [(1, len(lines), "config", name)], size, overlap)
        return parsed

    if rel_path.endswith(".tpl"):
        segments, marks = block_segments(lines, HELM_DEFINE, "code", min_lines=4)
        for pos, (no, name) in enumerate(marks):
            end = marks[pos + 1][0] - 1 if pos + 1 < len(marks) else len(lines)
            m = HELM_DEFINE.match(lines[no - 1])
            parsed.symbols.append(Symbol(m.group(1) if m else name, "helm_define", no, end, _sig(lines[no - 1])))
        parsed.chunks = make_chunks(lines, segments, size, overlap)
        return parsed

    # Split into YAML documents.
    docs: list[tuple[int, int]] = []
    start = 1
    for no, l in enumerate(lines, 1):
        if YAML_DOC_SEP.match(l):
            if no - 1 >= start:
                docs.append((start, no - 1))
            start = no + 1
    if start <= len(lines):
        docs.append((start, len(lines)))

    segments: list[Segment] = []
    any_kind = False
    for s, e in docs:
        doc = lines[s - 1 : e]
        kind_line = next(((i, YAML_KIND.match(l)) for i, l in enumerate(doc) if YAML_KIND.match(l)), None)
        if not kind_line:
            continue
        any_kind = True
        i, km = kind_line
        k8s_kind = km.group(1)
        name = "?"
        in_meta = False
        for l in doc:
            if l.startswith("metadata:"):
                in_meta = True
                continue
            if in_meta:
                nm = YAML_META_NAME.match(l)
                if nm:
                    name = nm.group(1)[:80]
                    break
                if l and not l.startswith((" ", "#")):
                    break
        parsed.symbols.append(Symbol(name, f"k8s:{k8s_kind}", s + i, e, f"{k8s_kind}/{name}"))
        if k8s_kind == "CustomResourceDefinition":
            crd_kind = next((m.group(1) for l in doc if (m := YAML_CRD_KIND.match(l))), name)
            parsed.symbols.append(Symbol(crd_kind, "crd", s + i, e, f"CRD {name}"))
        if k8s_kind in WEBHOOK_KINDS:
            parsed.symbols.append(Symbol(name, "webhook", s + i, e, f"{k8s_kind}/{name}"))
        segments.append((s, e, "config", f"{k8s_kind}/{name}"))

    if any_kind:
        parsed.chunks = make_chunks(lines, fill_gaps(len(lines), segments, "config", None), size, overlap)
        return parsed

    block, marks = block_segments(lines, YAML_TOP_KEY, "config", min_lines=12)
    for no, name in marks[:60]:
        parsed.symbols.append(Symbol(name.rstrip(":"), "config", no, no, _sig(lines[no - 1])))
    parsed.chunks = make_chunks(lines, block, size, overlap)
    return parsed


# --------------------------------------------------------------------------- Markdown / text

MD_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)")


def parse_markdown(language: str, lines: list[str], size: int, overlap: int) -> ParsedFile:
    parsed = ParsedFile(language, len(lines))
    in_fence = False
    marks: list[tuple[int, str]] = []
    for no, l in enumerate(lines, 1):
        if l.lstrip().startswith("```"):
            in_fence = not in_fence
        if in_fence:
            continue
        m = MD_HEADING.match(l)
        if m and len(m.group(1)) <= 3:
            marks.append((no, m.group(2).strip()))
            parsed.symbols.append(Symbol(m.group(2).strip()[:120], "heading", no, no, _sig(l)))
    if not marks:
        parsed.chunks = make_chunks(lines, [(1, len(lines), "doc", None)], size, overlap)
        return parsed
    segments: list[Segment] = []
    cur_start, cur_name = marks[0]
    if marks[0][0] > 1:
        segments.append((1, marks[0][0] - 1, "doc", None))
    for i, (no, name) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(lines) + 1
        if nxt - cur_start >= 6 or i + 1 == len(marks):
            segments.append((cur_start, nxt - 1, "doc", cur_name))
            if i + 1 < len(marks):
                cur_start, cur_name = marks[i + 1]
    parsed.chunks = make_chunks(lines, segments, size, overlap)
    return parsed


# --------------------------------------------------------------------------- shell / docker / make / json / config

SH_FUNC = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{?")
SH_SOURCE = re.compile(r"^\s*(?:source|\.)\s+[\"']?([^\s\"';]+)")


def parse_shell(lines: list[str], size: int, overlap: int) -> ParsedFile:
    parsed = ParsedFile("shell", len(lines))
    segments: list[Segment] = []
    idx = 0
    while idx < len(lines):
        l = lines[idx]
        m = SH_FUNC.match(l)
        if m and not l.lstrip().startswith("#"):
            end = idx
            for j in range(idx + 1, min(len(lines), idx + 1500)):
                if lines[j].rstrip() == "}":
                    end = j
                    break
            else:
                end = idx
            parsed.symbols.append(Symbol(m.group(1), "function", idx + 1, end + 1, _sig(l)))
            segments.append((idx + 1, end + 1, "code", m.group(1)))
            idx = end + 1
            continue
        sm = SH_SOURCE.match(l)
        if sm:
            parsed.relations.append(Relation("source", sm.group(1), idx + 1))
        idx += 1
    parsed.chunks = make_chunks(lines, fill_gaps(len(lines), segments, "code", "script"), size, overlap)
    return parsed


def parse_docker(lines: list[str], size: int, overlap: int) -> ParsedFile:
    parsed = ParsedFile("docker", len(lines))
    for no, l in enumerate(lines, 1):
        s = l.strip()
        m = re.match(r"(?i)^FROM\s+(?:--platform=\S+\s+)?(\S+)", s)
        if m:
            parsed.relations.append(Relation("image", m.group(1), no))
        if re.match(r"(?i)^(ENTRYPOINT|CMD)\b", s):
            parsed.symbols.append(Symbol(s.split()[0].upper(), "entrypoint", no, no, _sig(s)))
    parsed.chunks = make_chunks(lines, [(1, len(lines), "config", "Dockerfile")], size, overlap)
    return parsed


MAKE_TARGET = re.compile(r"^([A-Za-z0-9_./-]+)\s*:(?!=)")


def parse_make(lines: list[str], size: int, overlap: int) -> ParsedFile:
    parsed = ParsedFile("make", len(lines))
    for no, l in enumerate(lines, 1):
        m = MAKE_TARGET.match(l)
        if m and not m.group(1).startswith("."):
            parsed.symbols.append(Symbol(m.group(1), "make_target", no, no, _sig(l)))
    parsed.chunks = make_chunks(lines, [(1, len(lines), "config", None)], size, overlap)
    return parsed


def parse_json(rel_path: str, text: str, lines: list[str], size: int, overlap: int) -> ParsedFile:
    parsed = ParsedFile("json", len(lines))
    if rel_path.endswith("package.json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
        for section in ("dependencies", "devDependencies"):
            for dep in (data.get(section) or {}):
                line = next((i for i, l in enumerate(lines, 1) if f'"{dep}"' in l), 1)
                parsed.relations.append(Relation("import", dep, line))
        for script in (data.get("scripts") or {}):
            line = next((i for i, l in enumerate(lines, 1) if f'"{script}"' in l), 1)
            parsed.symbols.append(Symbol(script, "script", line, line, ""))
    parsed.chunks = make_chunks(lines, [(1, len(lines), "config", None)], size, overlap)
    return parsed


def parse_generic(language: str, lines: list[str], kind: str, size: int, overlap: int) -> ParsedFile:
    parsed = ParsedFile(language, len(lines))
    parsed.chunks = make_chunks(lines, [(1, len(lines), kind, None)], size, overlap)
    return parsed


# --------------------------------------------------------------------------- dispatch

def parse_file(rel_path: str, language: str, text: str, chunk_lines: int = 60, chunk_overlap: int = 10) -> ParsedFile:
    lines = text.splitlines()
    if language == "yaml" and "{{" in text:
        language = "helm"
    if language == "python":
        return parse_python(text, lines, chunk_lines, chunk_overlap)
    if language == "go":
        return parse_go(text, lines, chunk_lines, chunk_overlap)
    if language == "java":
        return parse_java(text, lines, chunk_lines, chunk_overlap)
    if language in ("typescript", "javascript"):
        return parse_js(language, text, lines, chunk_lines, chunk_overlap)
    if language in ("yaml", "helm"):
        return parse_yaml(rel_path, language, text, lines, chunk_lines, chunk_overlap)
    if language in ("markdown", "text"):
        return parse_markdown(language, lines, chunk_lines, chunk_overlap)
    if language == "shell":
        return parse_shell(lines, chunk_lines, chunk_overlap)
    if language == "docker":
        return parse_docker(lines, chunk_lines, chunk_overlap)
    if language == "make":
        return parse_make(lines, chunk_lines, chunk_overlap)
    if language == "json":
        return parse_json(rel_path, text, lines, chunk_lines, chunk_overlap)
    return parse_generic(language, lines, "config", chunk_lines, chunk_overlap)
