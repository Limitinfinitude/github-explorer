import os
from pathlib import Path
from typing import Union


PathLike = Union[str, Path]


class EvidencePathError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_evidence_path(workspace_root: PathLike, path: PathLike) -> str:
    """Return a workspace-relative POSIX path for persisted evidence."""
    root = Path(workspace_root).expanduser().resolve()
    raw_path = str(path)
    candidate_path = Path(raw_path.replace("\\", os.sep)).expanduser()
    if not candidate_path.is_absolute():
        candidate_path = root / candidate_path
    candidate = candidate_path.resolve()

    root_key = os.path.normcase(str(root))
    candidate_key = os.path.normcase(str(candidate))
    try:
        inside = os.path.commonpath((root_key, candidate_key)) == root_key
    except ValueError:
        inside = False
    if not inside:
        raise EvidencePathError(
            f"证据路径超出工作区: {candidate}",
            code="outside_workspace",
        )

    relative = os.path.relpath(str(candidate), str(root))
    return "." if relative == "." else relative.replace("\\", "/")
