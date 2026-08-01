from pathlib import Path

from artmach_assistant.indexing.dependency_graph import DependencyGraph
from artmach_assistant.indexing.dependency_resolver import DependencyResolver


def test_replacing_dependencies_drops_orphaned_display_paths(tmp_path: Path) -> None:
    graph = DependencyGraph()
    source = tmp_path / "source.py"
    old_dependency = tmp_path / "old.py"
    new_dependency = tmp_path / "new.py"

    graph.replace_dependencies(source, (old_dependency,))
    graph.replace_dependencies(source, (new_dependency,))

    assert str(old_dependency.resolve()) not in graph._display_paths.values()
    assert graph.dependencies_of(source) == (str(new_dependency.resolve()),)


def test_removing_source_drops_unreferenced_dependency_metadata(tmp_path: Path) -> None:
    graph = DependencyGraph()
    source = tmp_path / "source.py"
    dependency = tmp_path / "dependency.py"

    graph.replace_dependencies(source, (dependency,))
    graph.remove(source)

    assert graph.stats().nodes == 0
    assert graph._display_paths == {}


def test_package_wins_deterministically_over_same_named_module(tmp_path: Path) -> None:
    module = tmp_path / "feature.py"
    package = tmp_path / "feature"
    package.mkdir()
    package_init = package / "__init__.py"
    consumer = tmp_path / "consumer.py"

    module.write_text("VALUE = 'module'\n", encoding="utf-8")
    package_init.write_text("VALUE = 'package'\n", encoding="utf-8")
    consumer.write_text("import feature\n", encoding="utf-8")

    resolver = DependencyResolver(tmp_path)
    resolver.rebuild()

    assert resolver.graph.dependencies_of(consumer) == (str(package_init.resolve()),)
    assert package_init.resolve() in resolver.source_paths()
    assert module.resolve() not in resolver.source_paths()
