"""Dispose only the workspace created by this successful build invocation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuildWorkspace:
    parent: Path
    path: Path
    token: str

    @classmethod
    def create(cls, parent: Path) -> BuildWorkspace:
        parent = parent.absolute()
        parent.mkdir(parents=True, exist_ok=True)
        if parent.resolve() != parent:
            raise ValueError("Build workspace parent must not be an alias")
        path = Path(tempfile.mkdtemp(prefix="build-", dir=parent))
        token = uuid.uuid4().hex
        (path / ".invocation.json").write_text(json.dumps({"token": token, "pid": os.getpid()}))
        return cls(parent, path, token)

    def finish(self, output: Path, *, keep: bool = False) -> dict[str, object]:
        """Record the copied output; failures retain their workspaces for diagnosis."""
        if (
            self.path.parent != self.parent
            or not self.path.name.startswith("build-")
            or self.path.resolve(strict=True) != self.path.absolute()
            or self.parent.resolve(strict=True) != self.parent.absolute()
        ):
            raise ValueError("Build cleanup target is not this invocation's direct workspace")
        marker = self.path / ".invocation.json"
        if marker.is_symlink() or json.loads(marker.read_text()).get("token") != self.token:
            raise ValueError("Build cleanup ownership does not match this invocation")
        output = output.resolve(strict=True)
        if output.is_relative_to(self.path):
            raise ValueError("Final output must be copied outside disposable build work")
        record = {
            "format": 1,
            "build_id": self.path.name,
            "output_name": output.name,
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "output_bytes": output.stat().st_size,
            "cleanup": "retained",
        }
        if not keep:
            # The resolved absolute target was checked above. rmtree does not follow Windows junctions.
            try:
                shutil.rmtree(self.path)
            except OSError:
                record["cleanup"] = "unavailable; workspace retained for inspection"
            else:
                record["cleanup"] = "completed"
        receipt = self.parent / (self.path.name + ".receipt.json")
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2)
            stream.write("\n")
        return record
