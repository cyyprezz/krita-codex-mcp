from __future__ import annotations

import ast
import unittest
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SERVICE_HIERARCHIES = {
    "mcp": {
        "root": REPO_ROOT / "src" / "krita_codex_mcp",
        "facade": "core_service.py",
        "shared_providers": frozenset(),
    },
    "plugin": {
        "root": REPO_ROOT / "krita-plugin" / "kritacodexbridge",
        "facade": "service.py",
        # ServiceContextMixin is the intentional shared substrate for every feature mixin.
        "shared_providers": frozenset({"ServiceContextMixin"}),
    },
}

# Pair-level allowlist: callers may use members owned by the named provider mixin.
# Method names stay out of the manifest so internal refactors within an approved pair are stable.
ALLOWED_CROSS_MIXIN_DEPENDENCIES = {
    "mcp": frozenset(),
    "plugin": frozenset(
        {
            ("AnimationOperationsMixin", "AssetOperationsMixin"),
            ("AnimationOperationsMixin", "PackageOperationsMixin"),
            ("AssetOperationsMixin", "AnimationOperationsMixin"),
            ("AssetOperationsMixin", "RegionOperationsMixin"),
            ("ColorAdjustmentOperationsMixin", "ColorOperationsMixin"),
            ("ColorOperationsMixin", "ColorAdjustmentOperationsMixin"),
            ("ColorOperationsMixin", "PaintingOperationsMixin"),
            ("DocumentOperationsMixin", "LayerOperationsMixin"),
            ("DocumentOperationsMixin", "PaintingOperationsMixin"),
            ("DrawingOperationsMixin", "PaintingOperationsMixin"),
            ("LayerOperationsMixin", "LayerTransformOperationsMixin"),
            ("LayerTransformOperationsMixin", "TransformMaskOperationsMixin"),
            ("RegionOperationsMixin", "AnimationOperationsMixin"),
            ("RegionOperationsMixin", "AssetOperationsMixin"),
            ("RegionOperationsMixin", "PackageOperationsMixin"),
        }
    ),
}


def _mixin_class_definitions(root: Path) -> dict[str, tuple[Path, ast.ClassDef]]:
    definitions: dict[str, tuple[Path, ast.ClassDef]] = {}
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not node.name.endswith("Mixin"):
                continue
            if node.name in definitions:
                previous = definitions[node.name][0]
                raise AssertionError(
                    f"duplicate service mixin {node.name!r} in "
                    f"{previous.name} and {path.name}"
                )
            definitions[node.name] = (path, node)
    return definitions


def _service_mixins(
    root: Path, facade_name: str
) -> dict[str, tuple[Path, ast.ClassDef]]:
    definitions = _mixin_class_definitions(root)
    facade_tree = ast.parse(
        (root / facade_name).read_text(encoding="utf-8"),
        filename=str(root / facade_name),
    )
    facade = next(
        node
        for node in facade_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "KritaService"
    )
    mixin_names = [
        base.id
        for base in facade.bases
        if isinstance(base, ast.Name) and base.id.endswith("Mixin")
    ]
    missing = sorted(set(mixin_names) - definitions.keys())
    if missing:
        raise AssertionError(f"KritaService mixins have no class definition: {missing}")
    return {name: definitions[name] for name in mixin_names}


def _member_definitions(node: ast.ClassDef) -> list[tuple[str, str, int]]:
    members: list[tuple[str, str, int]] = []
    for statement in node.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.append((statement.name, "method", statement.lineno))
        elif isinstance(statement, ast.Assign):
            members.extend(
                (target.id, "class attribute", statement.lineno)
                for target in statement.targets
                if isinstance(target, ast.Name)
            )
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            members.append((statement.target.id, "class attribute", statement.lineno))
    return members


def _cross_mixin_dependencies(
    mixins: dict[str, tuple[Path, ast.ClassDef]], shared_providers: frozenset[str]
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], set[str]]]:
    owners: dict[str, list[str]] = defaultdict(list)
    for mixin_name, (_, node) in mixins.items():
        for member_name, _, _ in _member_definitions(node):
            owners[member_name].append(mixin_name)

    dependencies: set[tuple[str, str]] = set()
    evidence: dict[tuple[str, str], set[str]] = defaultdict(set)
    for caller, (path, node) in mixins.items():
        for candidate in ast.walk(node):
            if not (
                isinstance(candidate, ast.Attribute)
                and isinstance(candidate.value, ast.Name)
                and candidate.value.id == "self"
            ):
                continue
            providers = owners.get(candidate.attr, [])
            if len(providers) != 1 or providers[0] in {caller, *shared_providers}:
                continue
            dependency = (caller, providers[0])
            dependencies.add(dependency)
            evidence[dependency].add(f"{path.name}:{candidate.lineno} self.{candidate.attr}")
    return dependencies, evidence


class ServiceMixinContractTests(unittest.TestCase):
    def _hierarchy_mixins(self, hierarchy: str):
        config = SERVICE_HIERARCHIES[hierarchy]
        return _service_mixins(config["root"], config["facade"])

    def test_service_mixin_members_are_unique(self) -> None:
        for hierarchy in SERVICE_HIERARCHIES:
            with self.subTest(hierarchy=hierarchy):
                owners: dict[str, list[str]] = defaultdict(list)
                for mixin_name, (path, node) in self._hierarchy_mixins(hierarchy).items():
                    for member_name, kind, line in _member_definitions(node):
                        owners[member_name].append(
                            f"{mixin_name} ({kind} at {path.name}:{line})"
                        )
                duplicates = {
                    name: locations for name, locations in owners.items() if len(locations) > 1
                }
                self.assertFalse(
                    duplicates,
                    f"{hierarchy} service mixins define duplicate method or class-attribute "
                    f"names; rename or consolidate them to avoid MRO shadowing: {duplicates}",
                )

    def test_service_mixins_have_no_initializers_or_super_calls(self) -> None:
        for hierarchy in SERVICE_HIERARCHIES:
            with self.subTest(hierarchy=hierarchy):
                violations = []
                for mixin_name, (path, node) in self._hierarchy_mixins(hierarchy).items():
                    for statement in node.body:
                        if (
                            isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and statement.name == "__init__"
                        ):
                            violations.append(
                                f"{mixin_name}.__init__ at {path.name}:{statement.lineno}"
                            )
                    for candidate in ast.walk(node):
                        if (
                            isinstance(candidate, ast.Call)
                            and isinstance(candidate.func, ast.Name)
                            and candidate.func.id == "super"
                        ):
                            violations.append(
                                f"{mixin_name} calls super() at {path.name}:{candidate.lineno}"
                            )
                self.assertFalse(
                    violations,
                    f"{hierarchy} service mixins must remain stateless composition units; "
                    "initialization belongs in KritaService and cooperative super() chains are "
                    f"forbidden: {violations}",
                )

    def test_cross_mixin_dependencies_match_manifest(self) -> None:
        for hierarchy, config in SERVICE_HIERARCHIES.items():
            with self.subTest(hierarchy=hierarchy):
                mixins = self._hierarchy_mixins(hierarchy)
                allowed = ALLOWED_CROSS_MIXIN_DEPENDENCIES[hierarchy]
                shared_providers = config["shared_providers"]
                referenced_mixins = {
                    name for dependency in allowed for name in dependency
                } | shared_providers
                unknown = sorted(referenced_mixins - mixins.keys())
                self.assertFalse(
                    unknown,
                    f"{hierarchy} cross-mixin manifest references unknown mixins: {unknown}",
                )
                actual, evidence = _cross_mixin_dependencies(mixins, shared_providers)
                unexpected = actual - allowed
                stale = allowed - actual
                details = []
                if unexpected:
                    details.append(
                        "unexpected dependencies (review, then add the caller/provider pair): "
                        + "; ".join(
                            f"{caller} -> {provider} via {', '.join(sorted(evidence[pair]))}"
                            for pair in sorted(unexpected)
                            for caller, provider in [pair]
                        )
                    )
                if stale:
                    details.append(
                        "stale manifest entries (remove them): "
                        + "; ".join(
                            f"{caller} -> {provider}" for caller, provider in sorted(stale)
                        )
                    )
                self.assertFalse(
                    details,
                    f"{hierarchy} cross-mixin dependency manifest is out of date: "
                    + " | ".join(details),
                )


if __name__ == "__main__":
    unittest.main()
