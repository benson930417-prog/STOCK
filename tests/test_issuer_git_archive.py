import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import archive_issuer_data as archive


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE).strip()


@pytest.fixture
def repos(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
    paths = []
    for name in ("prod", "editor"):
        root = tmp_path / name
        subprocess.run(["git", "clone", str(remote), str(root)], check=True, capture_output=True)
        git(root, "config", "user.name", "Test")
        git(root, "config", "user.email", "test@example.invalid")
        git(root, "config", "core.autocrlf", "false")
        paths.append(root)
    prod, editor = paths
    for path in archive.PATHS:
        file = prod / path
        file.parent.mkdir(exist_ok=True)
        file.write_text('{"revision":1}\n')
    (prod / "app.py").write_text("version = 1\n")
    git(prod, "add", ".")
    git(prod, "commit", "-m", "initial")
    git(prod, "push", "origin", "main")
    git(editor, "pull", "--ff-only")
    return prod, editor, remote


def edit_and_push(editor, path="app.py", content="version = 2\n"):
    (editor / path).write_text(content)
    git(editor, "add", "--", path)
    git(editor, "commit", "-m", "parallel edit")
    git(editor, "push", "origin", "main")


def test_remote_code_preserved_local_code_index_and_secrets_untouched(repos):
    prod, editor, remote = repos
    edit_and_push(editor)
    (prod / archive.PATHS[0]).write_text('{"revision":2}\n')
    (prod / "app.py").write_text("local_unreviewed = True\n")
    (prod / "private.txt").write_text("must stay local\n")
    git(prod, "add", "private.txt")
    before_head = git(prod, "rev-parse", "HEAD")
    before_index = (prod / ".git/index").read_bytes()
    archive.archive(prod)
    assert git(remote, "show", "main:app.py") == b"version = 2"
    assert json.loads(git(remote, "show", f"main:{archive.PATHS[0]}")) == {"revision": 2}
    assert b"private.txt" not in git(remote, "ls-tree", "--name-only", "main")
    assert git(prod, "rev-parse", "HEAD") == before_head
    assert (prod / ".git/index").read_bytes() == before_index
    assert (prod / "app.py").read_text() == "local_unreviewed = True\n"
    # Subsequent archive uses its receipt even while deployed HEAD stays fixed.
    (prod / archive.PATHS[0]).write_text('{"revision":3}\n')
    archive.archive(prod)
    assert json.loads(git(remote, "show", f"main:{archive.PATHS[0]}")) == {"revision": 3}


def test_concurrent_push_retries_without_force(repos):
    prod, editor, remote = repos
    (prod / archive.PATHS[0]).write_text('{"revision":2}\n')
    real_git = archive.git
    pushed = False
    def race(root, *args, **kwargs):
        nonlocal pushed
        if args[0] == "push" and not pushed:
            pushed = True
            edit_and_push(editor)
        assert not any("force" in arg for arg in args)
        return real_git(root, *args, **kwargs)
    with patch.object(archive, "git", side_effect=race):
        result = archive.archive(prod)
    assert result["attempts"] == 2
    assert git(remote, "show", "main:app.py") == b"version = 2"
    assert json.loads(git(remote, "show", f"main:{archive.PATHS[0]}")) == {"revision": 2}


def test_upstream_evidence_conflict_is_not_overwritten(repos):
    prod, editor, remote = repos
    edit_and_push(editor, archive.PATHS[0], '{"revision":99}\n')
    (prod / archive.PATHS[0]).write_text('{"revision":2}\n')
    before = git(remote, "rev-parse", "main")
    with pytest.raises(RuntimeError, match="independently upstream"):
        archive.archive(prod)
    assert git(remote, "rev-parse", "main") == before
    assert not (prod / ".git/issuer-archive.json").exists()


def test_already_published_run_is_idempotent(repos):
    prod, _, remote = repos
    before = git(remote, "rev-parse", "main")
    archive.archive(prod)
    archive.archive(prod)
    assert git(remote, "rev-parse", "main") == before


def test_push_transport_failure_stays_failure(repos):
    prod, _, remote = repos
    (prod / archive.PATHS[0]).write_text('{"revision":2}\n')
    before = git(remote, "rev-parse", "main")
    real_git = archive.git
    def fail(root, *args, **kwargs):
        if args[0] == "push":
            raise RuntimeError("simulated connection error")
        return real_git(root, *args, **kwargs)
    with patch.object(archive, "git", side_effect=fail):
        with pytest.raises(RuntimeError, match="simulated connection error"):
            archive.archive(prod)
    assert git(remote, "rev-parse", "main") == before
    assert not (prod / ".git/issuer-archive.json").exists()
