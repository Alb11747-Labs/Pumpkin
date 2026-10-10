import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("head_source", Path(__file__).with_name("fobbitmc_head_source.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HeadSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fobbitmc-source-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_SYSTEM=os.devnull, GIT_ATTR_NOSYSTEM="1")
        self.git("init", "--quiet")
        self.git("config", "core.autocrlf", "false")
        (self.root / ".gitattributes").write_bytes(b"*.bat text eol=crlf\n*.txt text eol=lf\n")
        (self.root / "script.bat").write_bytes(b"first\nsecond\n")
        (self.root / "data.txt").write_bytes(b"immutable\n")
        self.commit()
        (self.root / "script.bat").unlink()
        self.git("checkout", "--", "script.bat")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], env=self.env, stderr=subprocess.PIPE)

    def commit(self):
        self.git("add", "--all")
        self.git("-c", "user.name=Source test", "-c", "user.email=source-test@example.invalid",
                 "commit", "--quiet", "-m", "fixture")
        self.head = self.git("rev-parse", "HEAD").decode().strip()

    def test_committed_crlf_is_accepted_and_source_is_unchanged(self):
        before = (self.root / "script.bat").read_bytes()
        self.assertEqual(before, b"first\r\nsecond\r\n")
        proof = module.source_provenance(self.root, self.head)
        self.assertEqual(set(proof["files"]), {".gitattributes", "script.bat", "data.txt"})
        self.assertEqual((self.root / "script.bat").read_bytes(), before)

    def test_normalized_clean_but_wrong_checkout_bytes_are_rejected(self):
        (self.root / "script.bat").write_bytes(b"first\nsecond\n")
        self.assertEqual(self.git("diff", "HEAD", "--"), b"")
        with self.assertRaisesRegex(ValueError, "HEAD-derived checkout"):
            module.source_provenance(self.root, self.head)

    def test_dirty_tracked_input_is_rejected(self):
        (self.root / "data.txt").write_bytes(b"changed\n")
        with self.assertRaisesRegex(ValueError, "Dirty tracked source"):
            module.source_provenance(self.root, self.head)

    def test_info_attributes_cannot_change_expected_bytes(self):
        (self.root / ".git/info/attributes").write_bytes(b"*.bat text eol=lf\n")
        module.source_provenance(self.root, self.head)

    def test_committed_custom_filter_is_rejected(self):
        (self.root / ".gitattributes").write_bytes(b"*.txt filter=custom\n")
        self.commit()
        with self.assertRaisesRegex(ValueError, "Unsupported committed checkout filter/driver"):
            module.source_provenance(self.root, self.head)


if __name__ == "__main__":
    unittest.main()
