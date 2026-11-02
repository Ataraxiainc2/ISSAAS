"""Regression tests for path traversal / symlink escape in unstream_dir.

See https://github.com/ansible/ansible-runner/issues/1548 (CWE-22 / CWE-59):
crafted zip members must not write files, or change modes/mtimes of files,
outside of the target directory.
"""

import base64
import io
import os
import stat
import zipfile

from ansible_runner.utils.streaming import unstream_dir


def _unstream_raw(raw_zip_bytes, target_directory):
    """Feed raw zip bytes through the same base64 wrapping the wire uses."""
    stream = io.BytesIO(base64.b64encode(raw_zip_bytes))
    unstream_dir(stream, len(raw_zip_bytes), str(target_directory))


def _symlink_zip_info(name):
    zi = zipfile.ZipInfo(name)
    zi.create_system = 3
    zi.external_attr = (0o777 | 0xA000) << 16
    return zi


def test_unstream_dir_symlink_escape_stays_inside_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        # symlink target/ pwn -> ../outside escapes the target directory
        archive.writestr(_symlink_zip_info("pwn"), "../outside")
        # written *through* the symlink, lands outside without the fix
        archive.writestr("pwn/owned.txt", "ESCAPED WRITE\n")

    try:
        _unstream_raw(zip_path.read_bytes(), target)
    except (ValueError, RuntimeError):
        pass  # refusing malicious archives outright is also acceptable

    assert not (outside / "owned.txt").exists()


def test_unstream_dir_member_traversal_does_not_touch_outside(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("innocent\n")
    victim.chmod(0o600)
    victim_mode = stat.S_IMODE(os.stat(victim).st_mode)
    victim_mtime = os.stat(victim).st_mtime_ns

    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        zi = zipfile.ZipInfo("../victim.txt")
        zi.create_system = 3
        zi.external_attr = 0o444 << 16
        archive.writestr(zi, "pwned\n")

    try:
        _unstream_raw(zip_path.read_bytes(), target)
    except (ValueError, RuntimeError):
        pass  # refusing malicious archives outright is also acceptable

    assert victim.read_text() == "innocent\n"
    assert stat.S_IMODE(os.stat(victim).st_mode) == victim_mode
    assert os.stat(victim).st_mtime_ns == victim_mtime
    assert not (target / "victim.txt").exists()
