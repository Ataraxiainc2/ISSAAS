"""Regression test for zip-slip (arbitrary file write) via unsanitised
ZIP member names in .key files (CWE-22).

`dir_file_sink` must never write outside its target directory, even when
handed member names containing `..` components or absolute paths.
"""
import os

from keynote_parser import file_utils


def test_dir_file_sink_does_not_write_outside_target_dir(tmp_path):
    target_dir = tmp_path / "extracted"
    target_dir.mkdir()

    escaped = tmp_path / "evil.txt"
    abs_escape = tmp_path / "abs-evil.txt"

    with file_utils.dir_file_sink(str(target_dir)) as sink:
        sink("../evil.txt", b"pwned")
        sink(os.path.join("..", "abs-parent.txt"), b"pwned")
        sink(str(abs_escape), b"pwned")
        sink("ok.txt", b"ok")

    assert not escaped.exists(), "traversal member escaped target dir"
    assert not (tmp_path / "abs-parent.txt").exists()
    assert not abs_escape.exists(), "absolute member path escaped target dir"
    # Benign members must still be written.
    assert (target_dir / "ok.txt").read_bytes() == b"ok"
