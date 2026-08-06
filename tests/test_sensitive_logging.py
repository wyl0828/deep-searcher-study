import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (
    PROJECT_ROOT / "deepsearcher",
    PROJECT_ROOT / "frontend",
)
PRODUCTION_FILES = (PROJECT_ROOT / "main.py",)
LOG_METHODS = {
    "print",
    "debug",
    "info",
    "warning",
    "error",
    "exception",
    "critical",
    "color_print",
}


def _call_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _contains_unsafe_exception_reference(node: ast.AST, exception_name: str) -> bool:
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name == "safe_exception_message":
            return False
        if name == "type" and len(node.args) == 1:
            argument = node.args[0]
            if isinstance(argument, ast.Name) and argument.id == exception_name:
                return False
    if isinstance(node, ast.Name) and node.id == exception_name:
        return True
    return any(
        _contains_unsafe_exception_reference(child, exception_name)
        for child in ast.iter_child_nodes(node)
    )


def test_production_logs_never_serialize_raw_exception_objects_or_tracebacks():
    violations = []
    source_paths = [
        path
        for root in PRODUCTION_ROOTS
        for path in root.rglob("*.py")
        if not {"tests", "node_modules", "dist"}.intersection(path.parts)
    ]
    source_paths.extend(path for path in PRODUCTION_FILES if path.exists())
    for path in source_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
            if not handler.name:
                continue
            for call in (node for node in ast.walk(handler) if isinstance(node, ast.Call)):
                if _call_name(call) not in LOG_METHODS:
                    continue
                raw_values = [*call.args, *(keyword.value for keyword in call.keywords)]
                if any(
                    _contains_unsafe_exception_reference(value, handler.name)
                    for value in raw_values
                ):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{call.lineno}")
                if any(
                    keyword.arg == "exc_info"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in call.keywords
                ):
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{call.lineno}")

    assert violations == []
