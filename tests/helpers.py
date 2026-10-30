import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pypatcher import cli, fileio  # noqa: E402


def doc(text):
    return fileio.decode(text.encode("utf-8"), "utf-8", "test")


class ProjectCase(unittest.TestCase):
    """Temporaeres Referenzprojekt (base) und Zielprojekt (target)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pypatcher-test-")
        self.base = os.path.join(self.tmp, "base")
        self.target = os.path.join(self.tmp, "target")
        os.makedirs(self.base)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, content, root=None, binary=False):
        path = os.path.join(root or self.base, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content if binary else content.encode("utf-8"))
        return path

    def read(self, rel, root=None):
        with open(os.path.join(root or self.target, *rel.split("/")), "rb") as handle:
            return handle.read()

    def run_cli(self, *argv):
        err = io.StringIO()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        self.last_out = out.getvalue()
        self.last_err = err.getvalue()
        return code

    def make_target(self):
        shutil.copytree(self.base, self.target)
        self.assertEqual(self.run_cli("init", "-q", "-p", self.target), 0, self.last_err)

    def seal(self, draft, name="p.ppatch.json", *extra):
        draft_path = os.path.join(self.tmp, name + ".draft.json")
        with open(draft_path, "w", encoding="utf-8") as handle:
            json.dump(draft, handle)
        out = os.path.join(self.tmp, name)
        code = self.run_cli("seal", "-q", draft_path, "--base", self.base, "-o", out, *extra)
        self.assertEqual(code, 0, self.last_err)
        return out

    def apply(self, patch, *extra):
        return self.run_cli("apply", "-q", "-y", patch, "-p", self.target, *extra)

    def snapshot(self, root):
        result = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != ".patcher"]
            for name in filenames:
                path = os.path.join(dirpath, name)
                with open(path, "rb") as handle:
                    result[os.path.relpath(path, root).replace(os.sep, "/")] = handle.read()
        return result
