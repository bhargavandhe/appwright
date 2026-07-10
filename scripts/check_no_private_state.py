"""Reject project-authored Python state variables whose names begin with an underscore."""

import ast
from pathlib import Path


class Violation:
    def __init__(self, path: Path, line: int, name: str) -> None:
        self.path = path
        self.line = line
        self.name = name

    def render(self) -> str:
        return f"{self.path}:{self.line}: private state name is forbidden: {self.name}"


def check_name(path: Path, line: int, name: str | None, violations: list[Violation]) -> None:
    if name is not None and name.startswith("_"):
        violations.append(Violation(path, line, name))


def check_target(path: Path, target: ast.expr, violations: list[Violation]) -> None:
    if isinstance(target, ast.Name):
        check_name(path, target.lineno, target.id, violations)
        return
    if isinstance(target, ast.Attribute):
        check_name(path, target.lineno, target.attr, violations)
        return
    if isinstance(target, ast.Starred):
        check_target(path, target.value, violations)
        return
    if isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            check_target(path, element, violations)


def inspect_file(path: Path) -> list[Violation]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                check_target(path, target, violations)
        elif isinstance(
            node,
            ast.AnnAssign
            | ast.AugAssign
            | ast.NamedExpr
            | ast.For
            | ast.AsyncFor
            | ast.comprehension,
        ):
            check_target(path, node.target, violations)
        elif isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                if item.optional_vars is not None:
                    check_target(path, item.optional_vars, violations)
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
            check_name(path, node.lineno, node.name, violations)
        elif isinstance(node, ast.MatchMapping):
            check_name(path, node.lineno, node.rest, violations)
        elif isinstance(node, ast.arg):
            check_name(path, node.lineno, node.arg, violations)
        elif isinstance(node, ast.alias):
            bound_name = node.asname if node.asname is not None else node.name.split(".")[0]
            check_name(path, node.lineno, bound_name, violations)
        elif isinstance(node, ast.Global | ast.Nonlocal):
            for name in node.names:
                check_name(path, node.lineno, name, violations)
    return violations


def python_files(root: Path) -> tuple[Path, ...]:
    selected: list[Path] = []
    for directory in (root / "src", root / "tests", root / "scripts"):
        if directory.exists():
            selected.extend(directory.rglob("*.py"))
    return tuple(sorted(selected))


def main() -> int:
    root = Path.cwd()
    violations: list[Violation] = []
    for path in python_files(root):
        violations.extend(inspect_file(path))
    for violation in violations:
        print(violation.render())
    return 1 if violations else 0


raise SystemExit(main())
