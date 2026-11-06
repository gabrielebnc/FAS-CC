"""Exercise nested checkouts and main guards using isolated local repositories."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
FILES = ("UPISAS/exemplars/crowdnav.py", "UPISAS/strategies/my_strategy.py", "run_challenge.py")


class TestNestedIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backbone = Path(self.temp.name).resolve() / "UPISAS"
        self.backbone.mkdir()
        self.repo = self.backbone / "FAS-CC"
        self.repo.mkdir()
        for directory in (self.backbone, self.repo):
            self.git(directory, "init", "-b", "development")
            self.git(directory, "config", "user.name", "Integration Test")
            self.git(directory, "config", "user.email", "test@example.invalid")
        package = self.backbone / "UPISAS"
        package.mkdir()
        (package / "strategy.py").write_text("# Original framework\n")
        for name in FILES:
            destination = self.repo / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        shutil.copytree(ROOT / "tools", self.repo / "tools")
        shutil.copytree(ROOT / ".githooks", self.repo / ".githooks")

    def git(self, directory, *args):
        return subprocess.run(["git", "-C", str(directory), *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def setup_links(self):
        subprocess.run([sys.executable, str(self.repo / "tools/integrate.py")],
                       check=True, capture_output=True, text=True)

    def test_links_are_single_source_and_setup_is_repeatable(self):
        old = self.backbone / "run_challenge.py"
        old.write_text("# Existing local work\n")
        self.setup_links()
        self.setup_links()
        for name in FILES:
            self.assertTrue((self.backbone / name).is_symlink())
            self.assertEqual((self.backbone / name).resolve(), self.repo / name)
        self.assertEqual((self.backbone / ".fas-cc-backup/run_challenge.py").read_text(),
                         "# Existing local work\n")
        (self.repo / FILES[0]).write_text("# Edited in submission repo\n")
        self.assertEqual((self.backbone / FILES[0]).read_text(), "# Edited in submission repo\n")
        for name in (*FILES, "FAS-CC/"):
            self.git(self.backbone, "check-ignore", name)

    def test_commit_and_push_guards_keep_main_to_exactly_three_paths(self):
        self.setup_links()
        self.git(self.repo, "symbolic-ref", "HEAD", "refs/heads/main")
        self.git(self.repo, "add", *FILES)
        self.git(self.repo, "commit", "-m", "Valid submission")
        good = self.git(self.repo, "rev-parse", "HEAD")
        (self.repo / "extra.txt").write_text("Not a submission file\n")
        self.git(self.repo, "add", "extra.txt")
        result = subprocess.run(["git", "-C", str(self.repo), "commit", "-m", "Invalid main"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Commit blocked", result.stderr)
        # Development permits tooling, but pushing that tree to remote main is refused.
        self.git(self.repo, "symbolic-ref", "HEAD", "refs/heads/development")
        self.git(self.repo, "commit", "-m", "Development extras")
        bad = self.git(self.repo, "rev-parse", "HEAD")
        hook = self.repo / ".git/hooks/pre-push"
        for sha, expected in [(good, 0), (bad, 1)]:
            result = subprocess.run([str(hook)], cwd=self.repo,
                                    input=f"refs/heads/development {sha} refs/heads/main {good}\n",
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)

    def test_linked_entrypoint_uses_backbone_framework_without_dashboard(self):
        for name in ("__init__.py", "strategy.py", "knowledge.py", "exceptions.py"):
            shutil.copyfile(ROOT / "UPISAS" / name, self.backbone / "UPISAS" / name)
        for directory in ("exemplars", "strategies"):
            package = self.backbone / "UPISAS" / directory
            package.mkdir(exist_ok=True)
            (package / "__init__.py").touch()
        self.setup_links()
        code = """
import pathlib, sys
sys.path.insert(0, sys.argv[1])
import run_challenge
import UPISAS.strategy
assert pathlib.Path(UPISAS.strategy.__file__).parent == pathlib.Path(sys.argv[1]) / 'UPISAS'
assert 'UPISAS.dashboard' not in sys.modules
assert 'tools.integrate' not in sys.modules
assert issubclass(run_challenge.MyStrategy, UPISAS.strategy.Strategy)
"""
        subprocess.run([sys.executable, "-I", "-c", code, str(self.backbone)],
                       check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
