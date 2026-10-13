from pathlib import Path

import pytest

from app.ingest.storage import save, sha256_of


def test_sha256_is_stable():
    assert sha256_of(b"hello") == sha256_of(b"hello")
    assert sha256_of(b"hello") != sha256_of(b"world")
    assert len(sha256_of(b"hello")) == 64


def test_save_writes_content_under_hash_path(tmp_path: Path):
    stored = save(b"pdf-bytes", "Acme IT Change Management.pdf", root=tmp_path)

    assert Path(stored.path).read_bytes() == b"pdf-bytes"
    assert stored.size == len(b"pdf-bytes")
    assert Path(stored.path).parent.name == stored.sha256[:2]
    assert Path(stored.path).name == f"{stored.sha256}.pdf"


def test_save_preserves_extension_case_insensitively(tmp_path: Path):
    stored = save(b"x", "Guideline.DOCX", root=tmp_path)
    assert stored.path.endswith(".docx")


def test_save_is_idempotent_for_identical_content(tmp_path: Path):
    a = save(b"same", "a.pdf", root=tmp_path)
    b = save(b"same", "b-different-name.pdf", root=tmp_path)
    assert a.path == b.path
    assert a.sha256 == b.sha256


def test_save_rejects_unsupported_extension(tmp_path: Path):
    with pytest.raises(ValueError, match="不支持的文件类型"):
        save(b"x", "malware.exe", root=tmp_path)


def test_save_rejects_path_traversal_in_filename(tmp_path: Path):
    stored = save(b"x", "../../etc/passwd.pdf", root=tmp_path)
    assert Path(stored.path).resolve().is_relative_to(tmp_path.resolve())
