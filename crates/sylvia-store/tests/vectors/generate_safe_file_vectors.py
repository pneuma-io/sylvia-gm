"""Reference vectors for the platform-independent parts of `infrastructure/safe_file.py`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_safe_file_vectors.py

Writes `safe_file_vectors.json`: `mount_filesystem_type` over hand-written and seeded random
`/proc/self/mountinfo` texts, and `classify_filesystem` for every type it names. Reference
tooling only; retired with the snapshot.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from sylvia_gm.infrastructure import safe_file  # noqa: E402

OUT = Path(__file__).resolve().with_name("safe_file_vectors.json")


def line(mount_point: str, filesystem: str, optional: str = "shared:1") -> str:
    """One mountinfo line: id, parent, dev, root, mount point, options, optional, -, type."""
    fields = ["36", "25", "8:1", "/", mount_point, "rw,relatime"]
    if optional:
        fields.extend(optional.split(" "))
    fields.extend(["-", filesystem, "/dev/sda1", "rw"])
    return " ".join(fields)


HAND = [
    (line("/", "ext4"), "/home/user/data"),
    (line("/", "ext4") + "\n" + line("/home", "xfs"), "/home/user/data"),
    (line("/", "ext4") + "\n" + line("/home", "xfs"), "/homework"),
    (line("/", "ext4") + "\n" + line("/home", "xfs"), "/home"),
    (line("/", "ext4") + "\n" + line("/mnt/data", "nfs4"), "/mnt/database/x"),
    (line("/", "ext4") + "\n" + line("/mnt/data", "nfs4"), "/mnt/data/x"),
    (line("/", "ext4") + "\n" + line("/srv", "tmpfs") + "\n" + line("/srv", "btrfs"), "/srv/a"),
    (line("/", "ext4") + "\n" + line("/srv", "btrfs") + "\n" + line("/srv", "tmpfs"), "/srv/a"),
    (line("/", "ext4") + "\n" + line("/my\\040disk", "xfs"), "/my disk/file"),
    (line("/", "ext4") + "\n" + line("/tab\\011dir", "xfs"), "/tab\tdir/f"),
    (line("/", "ext4") + "\n" + line("/back\\134slash", "btrfs"), "/back\\slash/f"),
    (line("/", "ext4") + "\n" + line("/data/", "xfs"), "/data/f"),
    (line("/", "ext4", optional=""), "/x"),
    (line("/", "ext4", optional="shared:1 master:2"), "/x"),
    ("36 25 8:1 / /broken rw", "/broken/x"),
    ("", "/x"),
    ("garbage\n" + line("/", "ext4"), "/x"),
    (line("/", "ext4") + "\r\n" + line("/opt", "xfs"), "/opt/y"),
    (line("/", "ext4") + "\x0b" + line("/opt", "xfs"), "/opt/y"),
    (line("/", "ext4") + "\x1c" + line("/opt", "xfs"), "/opt/y"),
    (line("/", "ext4") + " " + line("/opt", "xfs"), "/opt/y"),
    (line("/", "ext4") + "\x85" + line("/opt", "xfs"), "/opt/y"),
    (line("/opt", "xfs"), "relative/path"),
    (line("/", "fuse.sshfs"), "/x"),
    (line("/café", "xfs") + "\n" + line("/", "ext4"), "/café/x"),
    (line("/", "ext4") + "\n" + line("/a", "xfs").replace(" - ", " - - "), "/a/b"),
]

FILESYSTEMS = sorted(
    {"ext4", "xfs", "btrfs", "tmpfs", "overlay", "vfat", "ntfs3", "zfs", "squashfs", "fuse"}
    | set(safe_file._REMOTE_FILESYSTEMS)
)


def random_cases(count: int) -> list[tuple[str, str]]:
    rng = random.Random(20261121)
    points = ["/", "/a", "/a/b", "/a/bc", "/ab", "/a/b/c", "/m\\040n", "/x/", "/home", "/home/u"]
    paths = ["/a/b/c/d", "/a/bc", "/ab/c", "/a", "/m n/z", "/x/y", "/home/u/f", "/other", "/"]
    cases = []
    for _ in range(count):
        lines = [
            line(rng.choice(points), rng.choice(FILESYSTEMS))
            for _ in range(rng.randint(0, 6))
        ]
        cases.append(("\n".join(lines), rng.choice(paths)))
    return cases


def main() -> None:
    mount_cases = [
        {"mountinfo": text, "path": path, "type": safe_file.mount_filesystem_type(text, path)}
        for text, path in [*HAND, *random_cases(400)]
    ]
    classify = {name: safe_file.classify_filesystem(name) for name in FILESYSTEMS}
    vectors = {
        "python": sys.version.split()[0],
        "mount_filesystem_type": mount_cases,
        "classify_filesystem": classify,
        "classify_none": safe_file.classify_filesystem(None),
        "local_filesystems": sorted(safe_file.LOCAL_FILESYSTEMS),
        "remote_filesystems": sorted(safe_file._REMOTE_FILESYSTEMS),
    }
    text = json.dumps(vectors, indent=1, ensure_ascii=True) + "\n"
    OUT.write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
