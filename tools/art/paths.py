"""Strict relative paths shared by art inputs and patch destinations."""
from pathlib import Path, PurePosixPath


def relative_path(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or PurePosixPath(value).is_absolute()):
        raise ValueError("art paths must be safe POSIX relative paths")
    return value


def within_root(root: Path, value: str) -> Path:
    root = Path(root).resolve()
    path = (root / relative_path(value)).resolve()
    if not path.is_relative_to(root):
        raise ValueError("art path escaped its input root")
    return path
