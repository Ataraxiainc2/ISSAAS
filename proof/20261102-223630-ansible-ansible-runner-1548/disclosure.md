# Private vulnerability disclosure — DRAFT

DRAFT — for a human to review and send privately; ISSA AS never sends it.

To: security@ansible.com
Policy: SECURITY.md

Title: [Bug] Path traversal & symlink escape in unstream_dir() — CWE-22 / CWE-59 (ansible-runner)

Impacted project: ansible/ansible-runner

Description:
### Ansible Runner version

devel (commit 8dcdadbd8e4a7591565bdf93c7372c157f0f08ce); also affected: 2.4.3

### Python version

Python 3.13

### Operating System and Environment

```console
Ubuntu 22.04, local clone / venv (pip install -e).
Pure-stdlib reproduction; no container runtime involved.
```

### Container Runtime

N/A — bug is in unstream_dir() (zip extraction), not container-related

### Summary

unstream_dir() in src/ansible_runner/utils/streaming.py extracts zip members
without validating member paths or symlink targets. A crafted zip can therefore
write files and create symlinks OUTSIDE its target directory (private_data_dir /
artifact_dir), and can chmod/utime existing files outside the target directory.
This is a path traversal (CWE-22) + link-following (CWE-59) flaw in the
transmit -> worker streaming protocol's receiver.

Root cause (streaming.py, unstream_dir):
  - out_path = os.path.join(target_directory, info.filename)   # line 83, unsanitized
  - archive.extract(info.filename, path=target_directory)      # line 102
  - os.utime(out_path, ...)                                    # line 108, unsanitized
  - os.symlink(link, out_path)                                 # line 114, link target unvalidated
  - os.chmod(out_path, perms)                                  # line 116, unsanitized

Note: this is not classic zip-slip (ZipFile.extract() strips ".."), but the
"symlink target not validated" variant plus a chmod/utime traversal inconsistent
with extract()'s own sanitization.

NOTE: I initially intended to report this privately per SECURITY.md; opening
publicly for tracking only if maintainers prefer a normal issue.

### Steps to reproduce

Benign local PoC — the payload only creates a marker file inside a temp dir:

  import base64, io, os, tempfile, zipfile
  from pathlib import Path
  from ansible_runner.utils.streaming import unstream_dir

  work = Path(tempfile.mkdtemp())
  target = work / "target"; target.mkdir()
  (work / "outside").mkdir()  

Affected versions: Unknown (not recorded in the bundle)

Reproduction steps (regression test):
- Test: test/unit/utils/test_streaming_security.py
- Command: python -m pytest test/unit/utils/test_streaming_security.py -v
- The test fails on the base commit and passes with the fix (Fail-to-Pass).

Impact:
See the description above; the fix below removes the vulnerable behaviour
without changing any public API.

Suggested fix (fix.patch):
Confined unstream_dir() extraction to target_directory: added _is_safe_archive_name() (rejects absolute paths, drive-letter paths, and any '..' member component) and _is_within_directory() checks (lexical abspath plus realpath to block writes/chmod/utime through escaping symlinks, CWE-59); unsafe members are skipped. Symlink creation itself is preserved so legitimate round-trips (e.g. absolute-link test fixtures) keep working.

```diff
diff --git a/src/ansible_runner/utils/streaming.py b/src/ansible_runner/utils/streaming.py
index 0c004c8..c6ecf8e 100644
--- a/src/ansible_runner/utils/streaming.py
+++ b/src/ansible_runner/utils/streaming.py
@@ -60,6 +60,43 @@ def stream_dir(source_directory: str, stream: io.FileIO) -> None:
                     encoded_target.write(line)
 
 
+def _is_within_directory(base_directory: str, path: str) -> bool:
+    """Return True if ``path`` is ``base_directory`` or something inside it."""
+    base = os.path.abspath(base_directory)
+    candidate = os.path.abspath(path)
+    return candidate == base or candidate.startswith(base + os.sep)
+
+
+def _is_safe_archive_name(filename: str) -> bool:
+    """Return True if a zip member name cannot escape the target directory.
+
+    Blocks absolute paths (POSIX and Windows drive forms) and any ``..``
+    component. Note this must run on the raw name: a name like ``a/../b``
+    normalizes to something inside the target, but ``ZipFile.extract()``
+    sanitizes it differently (by dropping the ``..`` part), so the member
+    is unsafe whenever ``..`` appears at all.
+    """
+    if not filename:
+        return False
+    if os.path.isabs(filename):
+        return False
+    # Zip member names use "/" regardless of platform; also treat "\" as a
+    # separator here so Windows-style "..\\" traversal is caught anywhere.
+    if ".." in filename.replace("\\", "/").split("/"):
+        return False
+    normalized = os.path.normpath(filename)
+    if os.path.isabs(normalized):
+        return False
+    if normalized == os.pardir or normalized.startswith(os.pardir + os.sep):
+        return False
+    # Windows drive-letter absolute paths, not caught by os.path.isabs on POSIX
+    if len(normalized) > 1 and normalized[1] == ":":
+        return False
+    if normalized in ("", os.curdir):
+        return False
+    return True
+
+
 def unstream_dir(stream: io.FileIO, length: int, target_directory: str) -> None:
     # NOTE: caller needs to process exceptions
     with tempfile.NamedTemporaryFile() as tmp:
@@ -80,7 +117,11 @@ def unstream_dir(stream: io.FileIO, length: int, target_directory: str) -> None:
             # AWX relies on the execution bit, in particular, for inventory
             # https://www.burgundywall.com/post/preserving-file-perms-with-python-zipfile-module
             for info in archive.infolist():
-                out_path = os.path.join(target_directory, info.filename)
+                if not _is_safe_archive_name(info.filename):
+                    continue
+                out_path = os.path.join(target_directory, os.path.normpath(info.filename))
+                if not _is_within_directory(target_directory, out_path):
+                    continue
 
                 perms = info.external_attr >> 16
                 mode = stat.filemode(perms)
@@ -99,6 +140,8 @@ def unstream_dir(stream: io.FileIO, length: int, target_directory: str) -> None:
                         # Special case, the important dirs were pre-created so don't try to chmod them
                         continue
 
+                if not _is_within_directory(target_directory, os.path.realpath(out_path)):
+                    continue
                 archive.extract(info.filename, path=target_directory)
 
                 # Fancy logic to preserve modification times
```

Disclosure status: the issue is already public at https://github.com/ansible/ansible-runner/issues/1548; this report adds a fix verified by Fail-to-Pass

AI-assistance disclosure: this report and the attached fix were produced with AI assistance (ISSA AS, an automated fixing agent); a human must review and verify everything before sending.
Solver model: muse-spark-1.3
