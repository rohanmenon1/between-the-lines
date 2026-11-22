"""Package only a VSIX that contains a matching, previously built local worker."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

from build_worker import platform_target


ROOT = Path(__file__).resolve().parents[1]


def package_extension(target, output=None, root=ROOT):
    root = Path(root)
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    output = Path(output).absolute() if output else root / "artifacts" / f"{package['name']}-{package['version']}-{target}.vsix"
    if output.exists() or output.is_symlink():
        raise SystemExit("Output already exists. Preserve the reviewed package and choose a new --output path.")
    info_path = root / "worker" / "bin" / "build-info.json"
    executable_name = "btl-worker.exe" if target.startswith("win32-") else "btl-worker"
    executable = root / "worker" / "bin" / "btl-worker" / executable_name
    if not executable.is_file() or not info_path.is_file():
        raise SystemExit("Bundled worker missing. Run scripts/build_worker.py before packaging.")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    if info["target"] != target:
        raise SystemExit("Worker architecture does not match the requested VSIX target.")
    sources = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted((root / "worker" / "btl_agent").glob("*.py"))}
    if info.get("sources") != sources:
        raise SystemExit("Worker sources changed. Rebuild the bundled worker before packaging.")
    vsce_root = root / "node_modules" / "@vscode" / "vsce"
    if not (vsce_root / "package.json").is_file():
        raise SystemExit("Install development dependencies with npm install first.")
    vsce_package = json.loads((vsce_root / "package.json").read_text(encoding="utf-8"))
    entry = vsce_package["bin"]
    if isinstance(entry, dict):
        entry = entry["vsce"]
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required to build the extension package.")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Validate a disposable package first. Exclusive final creation also prevents
    # a concurrent packager from replacing an already reviewed release.
    with tempfile.TemporaryDirectory(prefix="btl-vsix-", dir=output.parent) as directory:
        staged = Path(directory) / "candidate.vsix"
        subprocess.run([
            node, str(vsce_root / entry), "package", "--no-dependencies", "--allow-missing-repository", "--no-rewrite-relative-links",
            "--target", target, "--out", str(staged),
        ], cwd=root, check=True)
        with zipfile.ZipFile(staged) as archive:
            names = archive.namelist()
            required = ["extension/config/model.json", f"extension/worker/bin/btl-worker/{executable_name}",
                        "extension/worker/bin/btl-worker/third-party-licenses/index.json"]
            for dependency in info.get("dependencies", []):
                required.extend(f"extension/worker/bin/btl-worker/third-party-licenses/{path}" for path in dependency["licenses"])
            if any(name not in names for name in required):
                raise SystemExit("Package validation failed: model manifest, worker, or required licenses were excluded.")
            if not any("llama" in name and name.endswith((".dll", ".so", ".dylib")) for name in names):
                raise SystemExit("Package validation failed: native inference library was excluded.")
        with staged.open("rb") as source, output.open("xb") as destination:
            shutil.copyfileobj(source, destination)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=platform_target())
    parser.add_argument("--output", type=Path, help="New VSIX path; existing files are never replaced.")
    arguments = parser.parse_args()
    output = package_extension(arguments.target, arguments.output)
    print(f"Packaged {output}")


if __name__ == "__main__":
    main()
