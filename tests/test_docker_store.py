from __future__ import annotations

from pathlib import Path

import pytest

from sextant_control.docker_store import ComposeLibrary, DockerStoreError


def make_library(tmp_path: Path, **overrides) -> ComposeLibrary:
    values = {
        "max_file_bytes": 1024,
        "max_files": 6,
        "max_total_bytes": 4096,
        "max_bundle_bytes": 16384,
    }
    values.update(overrides)
    library = ComposeLibrary(tmp_path / "compose", **values)
    library.ensure()
    return library


def files(compose: str = "services:\n  web:\n    image: nginx:latest\n", **extra) -> dict[str, str]:
    result = {"compose.yaml": compose}
    result.update(extra)
    return result


def test_deterministic_revision_hash_and_current_files(tmp_path: Path):
    library = make_library(tmp_path)
    first = files(**{".env": "TOKEN=abc\n", "override.yaml": "services: {}\n"})
    second = dict(reversed(list(first.items())))
    assert library.revision_hash({name: data.encode() for name, data in first.items()}) == library.revision_hash(
        {name: data.encode() for name, data in second.items()}
    )

    revision, manifest = library.save_revision("web", first)
    assert len(revision) == 64
    assert [item["name"] for item in manifest] == [".env", "compose.yaml", "override.yaml"]
    assert library.revision_hash(library.read_revision("web", revision)) == revision
    assert library.read_current("web")[".env"] == b"TOKEN=abc\n"
    assert (library.revision_dir("web", revision) / ".env").stat().st_mode & 0o777 == 0o600


def test_flat_file_name_and_size_rejections(tmp_path: Path):
    library = make_library(tmp_path)
    bad_sets = [
        {"override.yaml": "services: {}\n"},
        {"compose.yaml": "services: {}\n", "compose.yml": "services: {}\n"},
        {"compose.yaml": "services: {}\n", "sub/compose.yaml": "services: {}\n"},
        {"compose.yaml": "services: {}\n", "../escape.yaml": "services: {}\n"},
        {"compose.yaml": "services: {}\n", "/absolute.yaml": "services: {}\n"},
        {"compose.yaml": "services: {}\n", ".hidden.yaml": "services: {}\n"},
        {"compose.yaml": "services: {}\n", "notes.txt": "nope\n"},
        {"compose.yaml": "services:\n  web:\n    build: .\n"},
        {"compose.yaml": "x" * 1025},
        {"compose.yaml": "services: {}\n", **{f"o{i}.yaml": "services: {}\n" for i in range(6)}},
    ]
    for payload in bad_sets:
        with pytest.raises(DockerStoreError):
            library.save_revision("web", payload)


def test_invalid_slug_and_symlink_rejection(tmp_path: Path):
    library = make_library(tmp_path)
    with pytest.raises(DockerStoreError):
        library.save_revision("../escape", files())
    with pytest.raises(DockerStoreError):
        library.save_revision("UPPER", files())

    outside = tmp_path / "outside"
    outside.mkdir()
    (library.stacks_dir / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(DockerStoreError):
        library.stack_dir("link")


def test_atomic_revision_and_current_update(tmp_path: Path):
    library = make_library(tmp_path)
    first, _ = library.save_revision("web", files("services:\n  web:\n    image: nginx:1\n"))
    second, _ = library.save_revision("web", files("services:\n  web:\n    image: nginx:2\n"))
    assert first != second
    assert b"nginx:1" in library.read_revision("web", first)["compose.yaml"]
    assert b"nginx:2" in library.read_current("web")["compose.yaml"]
    assert b"nginx:1" in (library.revision_dir("web", first) / "compose.yaml").read_bytes()

    # Validation fails before touching the current files.
    with pytest.raises(DockerStoreError):
        library.save_revision("web", files("services:\n  web:\n    build: .\n"))
    assert b"nginx:2" in library.read_current("web")["compose.yaml"]


def test_revision_pruning_and_bundle(tmp_path: Path):
    library = make_library(tmp_path)
    revisions = []
    for index in range(4):
        revision, _ = library.save_revision("web", files(f"services:\n  web:\n    image: nginx:{index}\n"))
        revisions.append(revision)
    keep = {revisions[0], revisions[-1]}
    assert library.prune_revisions("web", keep) == 2
    assert not library.revision_dir("web", revisions[1]).exists()
    assert library.revision_dir("web", revisions[0]).exists()

    data = library.build_bundle("web", revisions[-1])
    assert data.startswith(b"\x1f\x8b")
    assert len(data) <= library.max_bundle_bytes


def test_directory_migration_and_refusal(tmp_path: Path):
    library = make_library(tmp_path)
    revision, _ = library.save_revision("web", files(**{".env": "A=1\n"}))
    new_root = tmp_path / "new-compose"
    counts = library.migrate_to(new_root)
    assert counts["stacks"] == 1
    assert counts["revisions"] == 1
    assert library.root == new_root.resolve()
    assert library.read_current("web")[".env"] == b"A=1\n"
    assert library.revision_dir("web", revision).exists()

    non_empty = tmp_path / "not-a-library"
    non_empty.mkdir()
    (non_empty / "keep.txt").write_text("keep")
    with pytest.raises(DockerStoreError):
        library.migrate_to(non_empty)


def test_root_validation(tmp_path: Path):
    with pytest.raises(DockerStoreError):
        ComposeLibrary(Path("relative")).validate_root(Path("relative"))
    with pytest.raises(DockerStoreError):
        ComposeLibrary(Path("/")).validate_root(Path("/"))
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(DockerStoreError):
        ComposeLibrary(link / "child").validate_root(link / "child")
