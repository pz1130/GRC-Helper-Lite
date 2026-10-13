"""原文落盘。

正式原文卷只由 worker 写入；API 只负责把上传内容放入暂存区并排队任务。
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

DOCUMENT_ROOT = Path("/data/documents")
ALLOWED_EXTENSIONS = frozenset({".pdf", ".docx"})
# 与生产 nginx client_max_body_size 对齐。开发栈没有那一层，应用自己挡。
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class StoredFile:
    path: str
    sha256: str
    size: int


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def save(content: bytes, filename: str, *, root: Path | None = None) -> StoredFile:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"不支持的文件类型 {extension or '(无扩展名)'}，"
            f"目前支持 {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    digest = sha256_of(content)
    base = root or DOCUMENT_ROOT
    target = base / digest[:2] / f"{digest}{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(content)

    return StoredFile(path=str(target), sha256=digest, size=len(content))
