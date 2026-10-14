"""Exercise the staged-file CLI against real, temporary Git indexes."""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from btl.annotate import AnnotationResult
from btl import git_cli


class GitCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self._git("init", "-q")

        repo_patch = patch("btl.git_cli._repo_root", return_value=self.root)
        repo_patch.start()
        self.addCleanup(repo_patch.stop)

        model_env = patch.dict(os.environ, {"BTL_OLLAMA_MODEL": "previous-model"})
        model_env.start()
        self.addCleanup(model_env.stop)

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True
        )

    def _stage(self, git_path: str, content: bytes) -> Path:
        path = self.root / git_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self._git("add", "--", git_path)
        return path

    def _indexed(self, git_path: str) -> bytes:
        return self._git("show", f":{git_path}").stdout

    def _run_cli(self, *options: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = git_cli.main(["annotate", "--staged", *options])
        return status, stdout.getvalue(), stderr.getvalue()

    @staticmethod
    def _result(
        source: str, valid: bool = True, status: str = "Validated"
    ) -> AnnotationResult:
        return AnnotationResult("", source, status, valid, 1)

    def test_clean_staged_file_updates_worktree_but_not_index(self):
        original = b"def greet():\n    return 'hello'\n"
        annotated = "# Greets a caller.\n" + original.decode("utf-8")
        path = self._stage("greet.py", original)

        with patch(
            "btl.git_cli.annotate_source", return_value=self._result(annotated)
        ) as annotate:
            status, output, error = self._run_cli("--model", "test-model")

        self.assertEqual((status, error), (0, ""))
        annotate.assert_called_once_with(original.decode("utf-8"), "local", "python")
        self.assertEqual(os.environ["BTL_OLLAMA_MODEL"], "test-model")
        self.assertEqual(path.read_bytes(), annotated.encode("utf-8"))
        self.assertEqual(self._indexed("greet.py"), original)
        self.assertEqual(self._git("diff", "--name-only").stdout, b"greet.py\n")
        self.assertIn("--- a/greet.py", output)
        self.assertIn("+# Greets a caller.", output)
        self.assertIn("stage the comments with git add", output)

    def test_partial_staging_is_rejected_before_annotation(self):
        original = b"def greet():\n    return 'hello'\n"
        path = self._stage("greet.py", original)
        working = original + b"\nprint(greet())\n"
        path.write_bytes(working)

        with patch("btl.git_cli.annotate_source") as annotate:
            status, output, error = self._run_cli()

        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        self.assertIn("unstaged changes found", error)
        annotate.assert_not_called()
        self.assertEqual(path.read_bytes(), working)
        self.assertEqual(self._indexed("greet.py"), original)

    def test_path_with_spaces_and_java_extension(self):
        original = b"class Helper {}\n"
        annotated = "// Explains helper.\n" + original.decode("utf-8")
        git_path = "src/my helper.java"
        path = self._stage(git_path, original)

        with patch(
            "btl.git_cli.annotate_source", return_value=self._result(annotated)
        ) as annotate:
            status, output, error = self._run_cli()

        self.assertEqual((status, error), (0, ""))
        annotate.assert_called_once_with(original.decode("utf-8"), "local", "java")
        self.assertEqual(path.read_bytes(), annotated.encode("utf-8"))
        self.assertEqual(self._indexed(git_path), original)
        self.assertIn("src/my helper.java", output)

    def test_dry_run_shows_diff_without_writing(self):
        original = b"def greet():\n    return 'hello'\n"
        annotated = "# Greets a caller.\n" + original.decode("utf-8")
        path = self._stage("greet.py", original)

        with patch("btl.git_cli.annotate_source", return_value=self._result(annotated)):
            status, output, error = self._run_cli("--dry-run")

        self.assertEqual((status, error), (0, ""))
        self.assertIn("+# Greets a caller.", output)
        self.assertIn("no files were changed", output)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self._indexed("greet.py"), original)
        self.assertEqual(self._git("diff", "--name-only").stdout, b"")

    def test_invalid_result_prevents_writes_to_all_staged_files(self):
        originals = {
            "a.py": b"def first():\n    pass\n",
            "b.py": b"def second():\n    pass\n",
        }
        paths = {name: self._stage(name, source) for name, source in originals.items()}
        valid = self._result("# First.\n" + originals["a.py"].decode("utf-8"))
        invalid = self._result(
            originals["b.py"].decode("utf-8"),
            valid=False,
            status="Rejected: changed code",
        )

        with patch(
            "btl.git_cli.annotate_source", side_effect=[valid, invalid]
        ) as annotate:
            status, output, error = self._run_cli()

        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        self.assertIn("b.py: Rejected: changed code", error)
        self.assertEqual(annotate.call_count, 2)
        for name, original in originals.items():
            self.assertEqual(paths[name].read_bytes(), original)
            self.assertEqual(self._indexed(name), original)

    def test_edits_during_inference_are_preserved_and_prevent_all_writes(self):
        originals = {
            "a.py": b"def first():\n    pass\n",
            "b.py": b"def second():\n    pass\n",
        }
        paths = {name: self._stage(name, source) for name, source in originals.items()}
        user_edit = originals["a.py"] + b"\nprint(first())\n"

        def annotate(source, model_choice, language):
            if "second" in source:
                paths["a.py"].write_bytes(user_edit)
            return self._result("# Explains this function.\n" + source)

        with patch("btl.git_cli.annotate_source", side_effect=annotate):
            status, output, error = self._run_cli()

        self.assertEqual(status, 1)
        self.assertIn("file changed during annotation", error)
        self.assertEqual(paths["a.py"].read_bytes(), user_edit)
        self.assertEqual(paths["b.py"].read_bytes(), originals["b.py"])
        for name, original in originals.items():
            self.assertEqual(self._indexed(name), original)

    def test_unstaging_during_inference_prevents_writes(self):
        original = b"def greet():\n    return 'hello'\n"
        path = self._stage("greet.py", original)

        def annotate(source, model_choice, language):
            self._git("rm", "--cached", "--", "greet.py")
            return self._result("# Greets a caller.\n" + source)

        with patch("btl.git_cli.annotate_source", side_effect=annotate):
            status, output, error = self._run_cli()

        self.assertEqual(status, 1)
        self.assertIn("Staging area changed during annotation", error)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self._git("ls-files").stdout, b"")

    def test_model_generation_warning_prevents_misleading_success(self):
        original = b"def greet():\n    return 'hello'\n"
        path = self._stage("greet.py", original)
        result = AnnotationResult(
            "",
            original.decode("utf-8"),
            "model generation failed",
            True,
            1,
            ("failed",),
        )

        with patch("btl.git_cli.annotate_source", return_value=result):
            status, output, error = self._run_cli()

        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        self.assertIn("model generation failed", error)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self._indexed("greet.py"), original)


if __name__ == "__main__":
    unittest.main()
