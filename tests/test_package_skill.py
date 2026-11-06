"""The released archive is portable and identical for the same Skill bytes."""
import hashlib
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_skill", ROOT / "package_skill.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def test_first_release_is_stable_and_published_assets_require_exact_checksums(self):
        ci_spec = importlib.util.spec_from_file_location("commit_release_ci", ROOT / "scripts/release_ci.py")
        ci = importlib.util.module_from_spec(ci_spec)
        ci_spec.loader.exec_module(ci)
        self.assertFalse(ci.release_is_prerelease("dennis8499/CommitReviewer", "0.1.0"))
        self.assertEqual(["commit-reviewer-0.1.0.zip", "commit-reviewer-0.1.0.zip.sha256"],
                         ci.asset_names("dennis8499/CommitReviewer", "0.1.0"))
        with tempfile.TemporaryDirectory() as directory:
            output = package.build(output_dir=directory)
            files = {output.name: output.read_bytes(), output.name + ".sha256": output.with_suffix(".zip.sha256").read_bytes()}
            ci.validate_checksums(files)
            files[output.name] += b"tampered"
            with self.assertRaises(ci.ProofError):
                ci.validate_checksums(files)

    def test_reproducible_complete_payload_and_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "skill"
            shutil.copytree(ROOT / "skills/commit-reviewer", source, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            output = package.build(source=source, output_dir=root / "dist", tag="v0.1.0")
            first = output.read_bytes()
            with ZipFile(output) as archive:
                expected = {"commit-reviewer/" + path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()}
                self.assertEqual(expected, set(archive.namelist()))
                self.assertEqual(b"0.1.0\n", archive.read("commit-reviewer/VERSION"))
            for path in source.rglob("*"):
                if path.is_file():
                    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            self.assertEqual(first, package.build(source=source, output_dir=root / "dist").read_bytes())
            self.assertEqual(hashlib.sha256(first).hexdigest() + "  " + output.name + "\n", output.with_suffix(".zip.sha256").read_text())

    def test_tag_mismatch_and_invalid_versions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "tag"):
                package.build(tag="v0.2.0", output_dir=directory)
            source = Path(directory) / "skill"
            source.mkdir()
            for version in ("01.0.0", "0.1.0-beta.1", "broken"):
                (source / "VERSION").write_text(version)
                with self.assertRaisesRegex(ValueError, "VERSION"):
                    package.build(source=source, output_dir=Path(directory) / "dist")
