"""Tree-sitter based logging-statement extractor for Go, Java and Python.

For every logging call we record the structural context it executes in (loop, retry, polling,
consumer, error path, ...) and classify the arguments that look like whole-object dumps.
This is the fact base that the detectors, the vector index and the LLM prompts are built on.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

import tree_sitter_go
import tree_sitter_java
import tree_sitter_python
from tree_sitter import Language, Node, Parser

from app.core.pricing import Level
from app.models import Confidence, LogStatement, PayloadArg, PayloadKind

LANGUAGES: dict[str, Language] = {
    "go": Language(tree_sitter_go.language()),
    "java": Language(tree_sitter_java.language()),
    "python": Language(tree_sitter_python.language()),
}
EXTENSIONS = {".go": "go", ".java": "java", ".py": "python"}

CALL_TYPES = {"go": "call_expression", "java": "method_invocation", "python": "call"}
FUNC_TYPES = {
    "go": {"function_declaration", "method_declaration"},
    "java": {"method_declaration", "constructor_declaration"},
    "python": {"function_definition"},
}
LOOP_TYPES = {
    "go": {"for_statement"},
    "java": {"for_statement", "enhanced_for_statement", "while_statement", "do_statement"},
    "python": {"for_statement", "while_statement"},
}
STRING_TYPES = {
    "go": {"interpreted_string_literal", "raw_string_literal"},
    "java": {"string_literal"},
    "python": {"string"},
}

# --- logger / level recognition ------------------------------------------------------------
_LOGGER_RECEIVER = re.compile(
    r"(?:(?<![A-Za-z])(?:log|logger|logging|logrus|logr|klog|glog|slog|zerolog|zap|sugar|lg)"
    r"|(?:Log|Logger|Logging|Logrus|Logr|Zap|Sugar|LOG|LOGGER))(?![a-z])"
)
_LEVEL_RE = re.compile(
    r"^(trace|verbose|debug|info|notice|warn|warning|error|err|critical|fatal|panic|dpanic|exception|print)"
    r"(f|w|ln|s)?$",
    re.I,
)
_LEVEL_MAP = {
    "trace": Level.VERBOSE,
    "verbose": Level.VERBOSE,
    "debug": Level.DEBUG,
    "info": Level.INFO,
    "notice": Level.INFO,
    "print": Level.INFO,
    "warn": Level.WARNING,
    "warning": Level.WARNING,
    "error": Level.ERROR,
    "err": Level.ERROR,
    "exception": Level.ERROR,
    "critical": Level.CRITICAL,
    "fatal": Level.CRITICAL,
    "panic": Level.CRITICAL,
    "dpanic": Level.CRITICAL,
}

# --- context heuristics ----------------------------------------------------------------------
_POLL_TEXT = re.compile(
    r"\b(sleep|ticker|NewTicker|time\.After|time\.Tick|Thread\.sleep|asyncio\.sleep|poll\w*)\b", re.I
)
_RETRY_TEXT = re.compile(r"\b(retry|retries|retrying|attempts?|backoff|tries|maxRetries|maxAttempts)\b", re.I)
_RETRY_CALLEE = re.compile(r"(retry|backoff|retryer|withretry)", re.I)
_POLL_FUNC = re.compile(r"(poll|watch|reconcil|heartbeat|tick|periodic|loop|sweep)", re.I)
_CONSUMER_FUNC = re.compile(
    r"(consum|onmessage|onevent|handlemessage|handleevent|processmessage|processevent|processjob|"
    r"processtask|listener|subscriber|worker|dispatch|schedul|cron|runjob|runtask|drain)",
    re.I,
)
_CONSUMER_ANNOT = re.compile(
    r"(KafkaListener|RabbitListener|JmsListener|SqsListener|StreamListener|EventListener|Scheduled|"
    r"celery|\.task|consumer|periodic|scheduled|listener)",
    re.I,
)
_RETRY_ANNOT = re.compile(r"(Retryable|retry|backoff|tenacity)", re.I)
_INIT_FUNC = re.compile(r"^(main|init|__init__|setup|bootstrap|start|startup|configure|run_server|new\w*)$", re.I)
_ENTRY_EXIT = re.compile(r"\b(entering|exiting|entered|exited|leaving)\b|^\s*(>>|<<|-->|<--)", re.I)
_ERR_COND_GO = re.compile(r"[eE]rr\w*\s*!=\s*nil|errors\.(Is|As)\(|\bIs[A-Z]\w*\(\s*\w*[eE]rr|[eE]rr\w*\s*==\s*\w+Err")
_ERR_NIL_EQ = re.compile(r"[eE]rr\w*\s*==\s*nil")
_PROPAGATE = {
    "go": re.compile(r"\breturn\b[^\n]*\b[eE]rr\w*|\breturn\b[^\n]*Errorf|\bpanic\("),
    "java": re.compile(r"\bthrow\b"),
    "python": re.compile(r"\braise\b"),
}
_FATAL = re.compile(r"(os\.Exit|sys\.exit|System\.exit|\bexit\()")
_EXPECTED = [
    (
        "validation",
        re.compile(
            r"validat|invalid|malformed|bad request|missing (required )?(field|param)|required field|"
            r"illegal ?argument|ValueError|ConstraintViolation|ErrInvalid|IsInvalid|must (be|not)",
            re.I,
        ),
    ),
    (
        "not_found",
        re.compile(
            r"not ?found|NoSuchElement|does not exist|doesn't exist|\b404\b|ErrNotFound|IsNotFound|KeyError|"
            r"FileNotFound",
            re.I,
        ),
    ),
    (
        "client_error",
        re.compile(r"unauthori[sz]ed|forbidden|\b40[13]\b|permission denied|access denied|unauthenticated", re.I),
    ),
    (
        "recoverable",
        re.compile(
            r"retry|retrying|will retry|try again|transient|temporar|timeout|timed out|conflict|already exists|"
            r"AlreadyExists|IsConflict|requeue|backoff|rate ?limit|\b429\b|\b503\b|unavailable|deadline",
            re.I,
        ),
    ),
]

# --- payload classification -----------------------------------------------------------------
_REQ = {"request", "req", "httprequest"}
_RESP = {"response", "resp", "res", "reply", "rsp"}
_PAY = {"payload", "body", "blob", "json", "raw", "content"}
_OBJ = {
    "resource",
    "obj",
    "object",
    "entity",
    "dto",
    "manifest",
    "spec",
    "template",
    "workspace",
    "inventory",
    "playbook",
    "headers",
    "record",
    "settings",
    "config",
    "cfg",
    "params",
    "parameters",
    "options",
    "input",
    "output",
    "vars",
    "variables",
    "metadata",
    "details",
    "model",
    "job",
    "task",
    "event",
    "agent",
}
_COLL = {
    "map",
    "list",
    "items",
    "array",
    "entries",
    "rows",
    "batch",
    "resources",
    "objects",
    "records",
    "jobs",
    "tasks",
    "events",
    "results",
    "workspaces",
    "agents",
}
_GENERIC = {"data", "info", "result", "value", "state", "item", "ctx"}
_SAFE_LAST = {
    "id",
    "ids",
    "name",
    "key",
    "status",
    "count",
    "size",
    "len",
    "length",
    "type",
    "kind",
    "code",
    "namespace",
    "region",
    "url",
    "path",
    "method",
    "duration",
    "elapsed",
    "latency",
    "version",
    "ms",
    "time",
    "timestamp",
    "uuid",
    "phase",
    "reason",
    "action",
    "cmd",
    "command",
    "host",
    "port",
    "err",
    "error",
    "e",
    "ex",
    "exception",
    "ctx",
    "context",
}
_SAFE_FUNCS = {
    "len",
    "size",
    "count",
    "length",
    "min",
    "max",
    "int",
    "float",
    "bool",
    "now",
    "since",
    "elapsed",
    "time",
    "string_id",
    "uuid",
}
_JSON_FUNCS = {
    "marshal",
    "marshalindent",
    "writevalueasstring",
    "tojson",
    "dumps",
    "stringify",
    "pformat",
    "pprint",
    "dump",
    "tojsonstring",
    "gettext",
}
_PEEL_FUNCS = {"tostring", "string", "str", "valueof", "sprint", "sprintf", "sprintln", "format", "repr"}
_ZAP_FIELD = re.compile(r"^(zap|slog|log|logrus)\.(Any|Reflect|Object|Stringer|String|Binary|ByteString|Error)$")
_VERBS_DUMP = re.compile(r"%\+v|%#v|%v|%s|%r|\{\}|\{[a-zA-Z_.]*\}")


@dataclass(frozen=True)
class _Arg:
    expr: str
    key: str = ""


_SERIALISED_ASSIGN = re.compile(
    r"\b(\w+)\s*(?:,\s*\w+\s*)?:?=\s*[\w.]*?(?:Marshal|MarshalIndent|writeValueAsString|toJson|toJSON|dumps|stringify)\(",
)


def _serialised_vars(func_text: str) -> set[str]:
    """Variables assigned from a JSON/serialisation call inside the enclosing function (e.g. ``b, _ := json.Marshal(x)``)."""
    return {m.group(1) for m in _SERIALISED_ASSIGN.finditer(func_text)} - {"_"}


def detect_language(path: str) -> str | None:
    for ext, lang in EXTENSIONS.items():
        if path.endswith(ext):
            return lang
    return None


def _tokens(name: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name)]


def _bump(conf: Confidence) -> Confidence:
    return {Confidence.LOW: Confidence.MEDIUM, Confidence.MEDIUM: Confidence.HIGH}.get(conf, Confidence.HIGH)


def classify_expr(expr: str, key_hint: str = "", fmt_dump: bool = False, _depth: int = 0) -> PayloadArg | None:
    """Return a PayloadArg if `expr` looks like a whole-object / payload dump, otherwise None."""
    expr = expr.strip()
    if not expr or _depth > 3 or re.fullmatch(r"-?[\d.]+|true|false|nil|null|None|True|False", expr):
        return None
    if expr[0] in "\"'`" or expr.startswith(('f"', "f'")):
        return None

    call = re.match(r"^(?P<callee>[\w.$]+)\((?P<args>.*)\)$", expr, re.S)
    if call:
        callee = call["callee"]
        last = callee.split(".")[-1].lower()
        inner_args = call["args"].strip()
        if last in _SAFE_FUNCS:
            return None
        if last in _JSON_FUNCS:
            inner = inner_args.split(",")[0] if inner_args else callee.rsplit(".", 1)[0]
            if _tokens(inner.split(".")[-1])[-1:] and _tokens(inner.split(".")[-1])[-1] in _SAFE_LAST:
                return None
            return PayloadArg(expr=expr, kind=PayloadKind.JSON_BLOB, confidence=Confidence.HIGH)
        if last in _PEEL_FUNCS:
            inner = inner_args.split(",")[-1] if inner_args else callee.rsplit(".", 1)[0]
            if "." not in callee and not inner_args:
                return None
            return classify_expr(inner, key_hint, fmt_dump, _depth + 1)
        name = callee.split(".")[-1]
    else:
        name = re.sub(r"\[.*?\]", "", expr).split(".")[-1].rstrip("&*")

    toks = _tokens(name)
    if not toks:
        return None
    if toks[-1] in _SAFE_LAST or (len(toks) == 1 and toks[0] in _SAFE_LAST):
        return None

    kind: PayloadKind | None = None
    conf = Confidence.MEDIUM
    tokset = set(toks) - {"get", "to", "the", "my"}
    if tokset & _REQ:
        kind, conf = PayloadKind.REQUEST, Confidence.HIGH
    elif tokset & _RESP:
        kind, conf = PayloadKind.RESPONSE, Confidence.HIGH
    elif tokset & _PAY:
        kind, conf = PayloadKind.PAYLOAD, Confidence.HIGH
    elif tokset & _COLL:
        kind, conf = PayloadKind.COLLECTION, Confidence.MEDIUM
    elif tokset & _OBJ:
        kind, conf = PayloadKind.OBJECT, Confidence.MEDIUM
    elif tokset & _GENERIC:
        kind, conf = PayloadKind.OBJECT, Confidence.LOW
    elif key_hint:
        ktoks = set(_tokens(key_hint))
        if ktoks & _REQ:
            kind, conf = PayloadKind.REQUEST, Confidence.LOW
        elif ktoks & _RESP:
            kind, conf = PayloadKind.RESPONSE, Confidence.LOW
        elif ktoks & (_PAY | _OBJ | _COLL):
            kind, conf = PayloadKind.OBJECT, Confidence.LOW
    if kind is None:
        return None
    if fmt_dump:
        conf = _bump(conf)
    return PayloadArg(expr=expr[:120], kind=kind, confidence=conf)


# --- tree helpers -----------------------------------------------------------------------------
class _Ctx:
    def __init__(self, src: bytes, lang: str):
        self.src = src
        self.lang = lang

    def text(self, node: Node | None) -> str:
        return "" if node is None else self.src[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def is_string(self, node: Node) -> bool:
        return node.type in STRING_TYPES[self.lang]

    def literal(self, node: Node) -> str:
        t = self.text(node)
        t = re.sub(r"^[rRbBfFuU]{0,2}(\"\"\"|'''|\"|'|`)", "", t)
        return re.sub(r"(\"\"\"|'''|\"|'|`)$", "", t)


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _call_parts(ctx: _Ctx, node: Node) -> tuple[str, str] | None:
    """Return (receiver_text, method_name) for a call node, or None."""
    lang = ctx.lang
    if lang == "go":
        fn = node.child_by_field_name("function")
        if fn is None or fn.type != "selector_expression":
            return None
        return ctx.text(fn.child_by_field_name("operand")), ctx.text(fn.child_by_field_name("field"))
    if lang == "java":
        obj, name = node.child_by_field_name("object"), node.child_by_field_name("name")
        return (ctx.text(obj), ctx.text(name)) if obj is not None and name is not None else None
    fn = node.child_by_field_name("function")
    if fn is None or fn.type != "attribute":
        return None
    return ctx.text(fn.child_by_field_name("object")), ctx.text(fn.child_by_field_name("attribute"))


def _log_level(ctx: _Ctx, node: Node) -> tuple[Level, str, str] | None:
    parts = _call_parts(ctx, node)
    if not parts:
        return None
    receiver, method = parts
    if receiver == "zap":  # zap.Error(err) / zap.String(...) are field constructors, not log calls
        return None
    m = _LEVEL_RE.match(method)
    if not m or not _LOGGER_RECEIVER.search(receiver):
        return None
    base = m.group(1).lower()
    if base == "print" and ctx.lang != "go":
        return None
    level = _LEVEL_MAP[base]
    if ".V(" in receiver and level is Level.INFO:  # klog.V(n).Info
        level = Level.VERBOSE
    return level, receiver, method


def _arg_nodes(ctx: _Ctx, node: Node) -> list[Node]:
    args = node.child_by_field_name("arguments")
    return [c for c in (args.named_children if args else []) if c.type != "comment"]


def _callee_name(ctx: _Ctx, call: Node) -> str:
    if ctx.lang == "java":
        return ctx.text(call.child_by_field_name("name"))
    fn = call.child_by_field_name("function")
    if fn is None:
        return ""
    if fn.type == "selector_expression":
        return ctx.text(fn.child_by_field_name("field"))
    if fn.type == "attribute":
        return ctx.text(fn.child_by_field_name("attribute"))
    return ctx.text(fn)


def _flatten(ctx: _Ctx, nodes: list[Node]) -> tuple[str, list[_Arg], bool]:
    """Return (message, value args with key hints, exc_info flag)."""
    message = ""
    out: list[_Arg] = []
    exc_info = False
    pending_key = ""

    def add_value(n: Node, key: str = "") -> None:
        out.append(_Arg(expr=ctx.text(n), key=key or pending_key))

    def visit(n: Node) -> None:
        nonlocal message, pending_key, exc_info
        t = n.type
        if ctx.is_string(n):
            interps = [c for c in _walk(n) if c.type == "interpolation"]
            if interps:  # python f-string
                if not message:
                    message = ctx.literal(n)
                for it in interps:
                    expr = it.child_by_field_name("expression")
                    if expr is not None:
                        add_value(expr)
            elif not message:
                message = ctx.literal(n)
            else:
                pending_key = ctx.literal(n).lower()
                return
        elif t == "keyword_argument":
            key = ctx.text(n.child_by_field_name("name"))
            val = n.child_by_field_name("value")
            if key in ("exc_info", "stack_info") and ctx.text(val) in ("True", "1"):
                exc_info = True
            elif key not in ("extra", "exc_info", "stack_info", "stacklevel") and val is not None:
                out.append(_Arg(expr=ctx.text(val), key=key))
            return
        elif t == "binary_expression" and ctx.lang == "java" and ctx.text(n.child_by_field_name("operator")) == "+":
            visit(n.child_by_field_name("left"))
            visit(n.child_by_field_name("right"))
        elif t == "binary_operator" and ctx.lang == "python" and ctx.text(n.child_by_field_name("operator")) == "%":
            visit(n.child_by_field_name("left"))
            right = n.child_by_field_name("right")
            if right is not None and right.type == "tuple":
                for c in right.named_children:
                    add_value(c)
            elif right is not None:
                add_value(right)
        elif t in {"call", "call_expression", "method_invocation"}:
            name = _callee_name(ctx, n)
            full = ctx.text(n.child_by_field_name("function")) if ctx.lang != "java" else name
            inner = _arg_nodes(ctx, n)
            if ctx.lang == "go" and _ZAP_FIELD.match(full) and inner:
                key = ctx.literal(inner[0]).lower() if ctx.is_string(inner[0]) else ""
                if full.endswith(".Error"):
                    out.append(_Arg(expr="err"))
                elif len(inner) > 1:
                    out.append(_Arg(expr=ctx.text(inner[1]), key=key))
            elif name in ("Sprintf", "format") and inner:
                visit_all(inner)
                if ctx.lang == "python" and name == "format":
                    obj = n.child_by_field_name("function")
                    base = obj.child_by_field_name("object") if obj is not None else None
                    if base is not None and ctx.is_string(base) and not message:
                        message = ctx.literal(base)
            else:
                add_value(n)
        else:
            add_value(n)
        pending_key = ""

    def visit_all(ns: list[Node]) -> None:
        for n in ns:
            visit(n)

    visit_all(nodes)
    return message, out, exc_info


def _classify_context(ctx: _Ctx, call: Node) -> dict:
    """Walk up the AST from the log call and collect the structural context."""
    lang = ctx.lang
    info: dict = {
        "function": None,
        "in_loop": False,
        "in_retry": False,
        "in_polling": False,
        "in_consumer": False,
        "in_error_path": False,
        "in_init": False,
        "guard": "",
        "guarded_calls": [],
        "propagates_error": False,
        "fatal": False,
        "func_node": None,
    }
    guards: list[str] = []
    child, node = call, call.parent
    while node is not None:
        t = node.type
        if t in FUNC_TYPES[lang]:
            info["func_node"] = node
            break
        if t in LOOP_TYPES[lang]:
            info["in_loop"] = True
            body = ctx.text(node)
            if _is_infinite(ctx, node) or _POLL_TEXT.search(body):
                info["in_polling"] = True
            if _RETRY_TEXT.search(body):
                info["in_retry"] = True
        elif t in {"call_expression", "method_invocation", "call"} and node is not call:
            callee = _callee_name(ctx, node)
            if callee in {"forEach", "ForEach"}:
                info["in_loop"] = True
            elif _RETRY_CALLEE.search(callee) and child.type in {
                "func_literal",
                "lambda_expression",
                "argument_list",
                "arguments",
                "block",
            }:
                info["in_retry"] = True
        elif lang == "go" and t == "if_statement":
            cond = ctx.text(node.child_by_field_name("condition"))
            cons, alt = node.child_by_field_name("consequence"), node.child_by_field_name("alternative")
            in_cons = child == cons
            if (in_cons and _ERR_COND_GO.search(cond)) or (child == alt and _ERR_NIL_EQ.search(cond)):
                _mark_error(ctx, info, node, cons if in_cons else alt, cond)
            guards.append(cond[:120])
        elif lang == "java" and t == "catch_clause":
            types = ctx.text(next((c for c in node.children if c.type == "catch_formal_parameter"), None))
            _mark_error(ctx, info, node, node.child_by_field_name("body"), types)
            guards.append(types[:120])
            try_node = node.parent
            if try_node is not None:
                body = try_node.child_by_field_name("body")
                info["guarded_calls"] = _calls_in(ctx, body)
        elif lang == "python" and t == "except_clause":
            header = ctx.text(node).split(":", 1)[0]
            _mark_error(ctx, info, node, node.children[-1], header)
            guards.append(header[:120])
            try_node = node.parent
            if try_node is not None:
                info["guarded_calls"] = _calls_in(ctx, try_node.child_by_field_name("body"))
        elif lang == "java" and t == "if_statement" or lang == "python" and t == "if_statement":
            guards.append(ctx.text(node.child_by_field_name("condition"))[:120])
        child, node = node, node.parent
    info["guard"] = " | ".join(g for g in guards if g)[:300]
    return info


def _is_infinite(ctx: _Ctx, loop: Node) -> bool:
    lang = ctx.lang
    if lang == "go":
        return not [c for c in loop.named_children if c.type != "block"]
    if lang == "java":
        cond = loop.child_by_field_name("condition")
        return cond is None or ctx.text(cond).strip("() ") == "true"
    cond = loop.child_by_field_name("condition")
    return loop.type == "while_statement" and ctx.text(cond) in ("True", "1")


def _calls_in(ctx: _Ctx, node: Node | None) -> list[str]:
    if node is None:
        return []
    names = []
    for n in _walk(node):
        if n.type == CALL_TYPES[ctx.lang] and not _log_level(ctx, n):
            nm = _callee_name(ctx, n)
            if nm and nm not in names:
                names.append(nm)
    return names[:6]


def _mark_error(ctx: _Ctx, info: dict, owner: Node, block: Node | None, guard_text: str) -> None:
    info["in_error_path"] = True
    if block is not None:
        if _PROPAGATE[ctx.lang].search(ctx.text(block)):
            info["propagates_error"] = True
        if _FATAL.search(ctx.text(block)):
            info["fatal"] = True
    if ctx.lang == "go" and not info["guarded_calls"]:
        init = owner.child_by_field_name("initializer")
        if init is not None:
            info["guarded_calls"] = _calls_in(ctx, init)
        else:
            prev = owner.prev_named_sibling
            if prev is not None and prev.type in {
                "short_var_declaration",
                "assignment_statement",
                "expression_statement",
                "var_declaration",
            }:
                info["guarded_calls"] = _calls_in(ctx, prev)


def _decorators(ctx: _Ctx, func: Node) -> str:
    if ctx.lang == "java":
        mods = next((c for c in func.children if c.type == "modifiers"), None)
        return ctx.text(mods)
    if ctx.lang == "python" and func.parent is not None and func.parent.type == "decorated_definition":
        return " ".join(ctx.text(c) for c in func.parent.children if c.type == "decorator")
    return ""


def _snippet(ctx: _Ctx, node: Node) -> str:
    lines = ctx.text(node).splitlines()
    first = " ".join(s.strip() for s in lines[:3])
    return first[:220]


def extract_file(repository: str, path: str, source: bytes, language: str | None = None) -> list[LogStatement]:
    lang = language or detect_language(path)
    if lang not in LANGUAGES:
        return []
    parser = Parser(LANGUAGES[lang])
    tree = parser.parse(source)
    ctx = _Ctx(source, lang)
    statements: list[LogStatement] = []

    for node in _walk(tree.root_node):
        if node.type != CALL_TYPES[lang]:
            continue
        found = _log_level(ctx, node)
        if not found:
            continue
        level, _receiver, method = found
        message, values, exc_info = _flatten(ctx, _arg_nodes(ctx, node))
        info = _classify_context(ctx, node)
        func = info.pop("func_node")
        fname = ctx.text(func.child_by_field_name("name")) if func is not None else None
        decorators = _decorators(ctx, func) if func is not None else ""

        info["in_retry"] = info["in_retry"] or bool(
            (fname and re.search(r"retry|backoff", fname, re.I)) or _RETRY_ANNOT.search(decorators)
        )
        if fname:
            info["in_polling"] = info["in_polling"] or bool(_POLL_FUNC.search(fname))
            info["in_consumer"] = bool(_CONSUMER_FUNC.search(fname)) or bool(_CONSUMER_ANNOT.search(decorators))
            info["in_init"] = bool(_INIT_FUNC.match(fname)) or (
                func is not None and func.type == "constructor_declaration"
            )
        info["in_consumer"] = info["in_consumer"] or bool(_CONSUMER_ANNOT.search(decorators))

        fmt_dump = bool(re.search(r"%\+v|%#v", message)) or "%r" in message
        serialised = _serialised_vars(ctx.text(func)) if func is not None else set()
        payloads: list[PayloadArg] = []
        for a in values:
            pa = classify_expr(a.expr, a.key, fmt_dump)
            if pa is None and serialised:
                bare = re.sub(r"^(?:string|str|String\.valueOf)\((\w+)\)$", r"\1", a.expr.strip())
                if bare in serialised:
                    pa = PayloadArg(expr=a.expr[:120], kind=PayloadKind.JSON_BLOB, confidence=Confidence.HIGH)
            if pa and all(p.expr != pa.expr for p in payloads):
                payloads.append(pa)

        logs_exc = (
            exc_info
            or method.lower() == "exception"
            or any(
                re.fullmatch(r"(err|error|e|ex|exc|exception|\w*[eE]rr)", a.expr)
                or "Error(" in a.expr
                or a.expr.endswith((".Error()", ".getMessage()", "getStackTrace()"))
                for a in values
            )
        )
        if lang == "go" and method.lower().startswith(("fatal", "panic")):
            info["fatal"] = True

        guard_msg = f"{info['guard']} {message}"
        expected = ""
        if info["in_error_path"] or level in (Level.ERROR, Level.CRITICAL):
            for label, rx in _EXPECTED:
                if rx.search(guard_msg):
                    expected = label
                    break

        entry_exit = bool(_ENTRY_EXIT.search(message))
        line, end_line = node.start_point[0] + 1, node.end_point[0] + 1
        sid = hashlib.sha1(f"{repository}:{path}:{node.start_byte}".encode()).hexdigest()[:10]  # noqa: S324
        statements.append(
            LogStatement(
                id=sid,
                repository=repository,
                file=path,
                line=line,
                end_line=end_line,
                language=lang,
                level=level,
                logger_call=f"{_receiver}.{method}",
                message=message[:300],
                args=[a.expr[:120] for a in values][:8],
                function=fname,
                in_loop=info["in_loop"],
                in_retry=info["in_retry"],
                in_polling=info["in_polling"],
                in_consumer=info["in_consumer"],
                in_error_path=info["in_error_path"],
                in_init=info["in_init"],
                guard=info["guard"],
                expected_condition=expected,
                guarded_calls=info["guarded_calls"],
                propagates_error=info["propagates_error"],
                logs_exception=bool(logs_exc),
                fatal=info["fatal"],
                payload_args=payloads,
                entry_exit=entry_exit,
                snippet=_snippet(ctx, node),
            )
        )
    statements.sort(key=lambda s: s.line)
    return statements
