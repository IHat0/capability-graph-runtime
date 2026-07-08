"""Deterministic extraction of behavioral requirements from Python tests."""

import ast


def extract_test_assertion_checklist(test_files: dict[str, str]) -> list[str]:
    """Extract concise assertion and expected-exception requirements."""
    checklist: list[str] = []

    def add(item: str) -> None:
        concise = " ".join(item.strip().split())
        if concise and concise not in checklist:
            checklist.append(concise)

    for source in test_files.values():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in tree.body:
                if isinstance(node, ast.Assert):
                    requirement = f"assert {ast.unparse(node.test)}"
                    if node.msg is not None:
                        requirement += f": {ast.unparse(node.msg)}"
                    add(requirement)
                elif isinstance(node, ast.Try):
                    expected = {
                        ast.unparse(handler.type)
                        for handler in node.handlers
                        if handler.type is not None
                    }
                    if "ValueError" in expected:
                        for statement in node.body:
                            for child in ast.walk(statement):
                                if isinstance(child, ast.Call):
                                    add(
                                        f"{ast.unparse(child)} should raise ValueError"
                                    )
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    add(ast.unparse(node.value))
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    value = node.value
                    if isinstance(value, ast.Call):
                        add(ast.unparse(node))
        else:
            for line in source.splitlines():
                lowered = line.lower()
                if line.lstrip().startswith("assert ") or any(
                    marker in lowered
                    for marker in ("expected", "got", "must", "should", "raises")
                ):
                    add(line)
    return checklist
