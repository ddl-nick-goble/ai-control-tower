"""Filesystem type lookup (/proc/mounts). A leaf module: it must not import anything from `backend`, because
demo.default_db_path() uses it during `import backend`, before backend.db binds DB_PATH."""
import logging
from pathlib import Path

log = logging.getLogger(__name__)

NETWORK_FS = ("nfs", "nfs4", "cifs", "smb", "smb2", "smb3", "smbfs", "ceph", "glusterfs", "lustre", "9p",
              "fuse.sshfs", "fuse.s3fs", "fuse.gcsfuse", "fuse.goofys", "fuse.rclone", "fuse.juicefs")


def _unescape_mount(s: str) -> str:
    for esc, ch in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        s = s.replace(esc, ch)
    return s


def _read_mounts():
    """Mount table as [(mountpoint, fstype)], or None when /proc/mounts is unreadable."""
    out = []
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    out.append((_unescape_mount(parts[1]), parts[2]))
    except OSError as e:
        log.warning("cannot read /proc/mounts (%s); cannot tell the DB filesystem type, "
                    "falling back to the safe DELETE journal mode", e)
        return None
    return out


def fs_type(path, mounts=None) -> str:
    """Filesystem type of `path`: longest-prefix match of its resolved path against the mount table."""
    if mounts is None:
        mounts = _read_mounts() or []
    p = str(Path(path).expanduser().resolve())
    best, best_len = "unknown", -1
    for mp, fst in mounts:
        mp_n = mp.rstrip("/")
        if (p == mp_n or p.startswith(mp_n + "/") or mp_n == "") and len(mp_n) >= best_len:
            best, best_len = fst, len(mp_n)
    return best


def is_network_fs(path, mounts=None) -> bool:
    return fs_type(path, mounts).lower() in NETWORK_FS
