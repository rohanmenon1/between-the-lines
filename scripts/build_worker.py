"""Freeze the worker and native CPU runtime; no end-user Python installation."""

import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DISTRIBUTIONS = (
    "llama-cpp-python", "tree-sitter", "tree-sitter-java", "numpy",
    "Jinja2", "MarkupSafe", "diskcache", "typing_extensions", "pyinstaller",
)


def platform_target():
    system = {"Windows": "win32", "Linux": "linux", "Darwin": "darwin"}.get(platform.system())
    architecture = {"AMD64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine())
    if not system or not architecture:
        raise SystemExit("Unsupported build platform. Build on the target operating system and architecture.")
    return f"{system}-{architecture}"


def license_file(path):
    """Include installed license directories as well as conventional file names."""
    return any(part.casefold() == "licenses" for part in path.parts) or path.name.casefold().startswith(("license", "copying", "notice"))


def bundle_licenses(destination):
    """Copy exact installed notices and record the versions actually frozen."""
    destination.mkdir(exist_ok=True)
    dependencies = []
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        # Some Unix Python installations put the same file under the stdlib.
        import sysconfig
        python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        raise SystemExit("The Python installation license was not found; it must accompany the worker.")
    python_target = destination / "Python" / "LICENSE.txt"
    python_target.parent.mkdir(exist_ok=True)
    shutil.copy2(python_license, python_target)
    dependencies.append({"name": "Python", "version": platform.python_version(), "licenses": ["Python/LICENSE.txt"]})
    for package in RUNTIME_DISTRIBUTIONS:
        distribution = importlib.metadata.distribution(package)
        copied = []
        for relative in distribution.files or []:
            if not license_file(relative):
                continue
            original = Path(distribution.locate_file(relative))
            if not original.is_file():
                continue
            # Preserve subdirectories because NumPy distributes several upstream licenses.
            target = destination / package / str(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            copied.append(target.relative_to(destination).as_posix())
        if not copied:
            raise SystemExit(f"No installed license was found for {package}; packaging must retain its notice.")
        dependencies.append({"name": package, "version": distribution.version, "licenses": sorted(copied)})
    # The binding's MIT license and the native llama.cpp MIT license have
    # different copyright holders. The wheel need not include the latter.
    specification = importlib.util.find_spec("llama_cpp")
    native = Path(specification.origin).parent / "lib" if specification and specification.origin else None
    native_notices = [path for path in native.rglob("*") if path.is_file() and license_file(path)] if native else []
    vendored = ROOT / "worker" / "licenses" / "llama.cpp-LICENSE"
    if vendored.is_file():
        native_notices.append(vendored)
    if not native_notices:
        raise SystemExit("Native llama.cpp license missing. Retain its upstream license at worker/licenses/llama.cpp-LICENSE.")
    native_files = []
    for index, original in enumerate(native_notices):
        target = destination / "llama.cpp" / f"{index}-{original.name}"
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(original, target)
        native_files.append(target.relative_to(destination).as_posix())
    dependencies.append({"name": "llama.cpp", "version": f"bundled with llama-cpp-python {importlib.metadata.version('llama-cpp-python')}", "licenses": native_files})
    (destination / "index.json").write_text(json.dumps(dependencies, indent=2) + "\n", encoding="utf-8")
    return dependencies


def source_hashes():
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "worker" / "btl_agent").glob("*.py"))}


def main():
    target = platform_target()
    sources = source_hashes()
    for package in ("pyinstaller", "llama-cpp-python", "tree-sitter", "tree-sitter-java"):
        try:
            importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            raise SystemExit(f"Missing build dependency: {package}. See README development setup.") from None
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    (build / "worker_entry.py").write_text("from btl_agent.__main__ import main\n\nmain()\n", encoding="utf-8")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
        "--distpath", str(ROOT / "worker" / "bin"),
        "--workpath", str(build / "pyinstaller"),
        str(ROOT / "worker" / "worker.spec"),
    ], cwd=ROOT, check=True)
    executable = ROOT / "worker" / "bin" / "btl-worker" / ("btl-worker.exe" if sys.platform == "win32" else "btl-worker")
    if not executable.is_file():
        raise SystemExit("PyInstaller did not produce the worker executable.")
    # Test the actual frozen interpreter and parsers, without loading model weights.
    request = "\n".join(json.dumps(item) for item in [
        {"id": "1", "method": "status"},
        {"id": "2", "method": "shutdown"},
    ]) + "\n"
    completed = subprocess.run([str(executable)], input=request, text=True, capture_output=True, timeout=30, check=True)
    try:
        messages = [json.loads(line) for line in completed.stdout.splitlines()]
        assert messages[0]["result"]["protocolVersion"] == 1
        assert messages[1]["result"]["stopped"] is True
    except (ValueError, IndexError, KeyError, AssertionError):
        raise SystemExit("Frozen worker protocol smoke test failed.") from None
    native = list(executable.parent.rglob("*llama*"))
    if not any(path.suffix.lower() in (".dll", ".so", ".dylib") or ".so." in path.name for path in native):
        raise SystemExit("The frozen worker is missing the native llama.cpp library.")
    dependencies = bundle_licenses(executable.parent / "third-party-licenses")
    if source_hashes() != sources:
        raise SystemExit("Worker sources changed during the build. Rebuild before packaging.")
    (executable.parent.parent / "build-info.json").write_text(json.dumps({
        "target": target,
        "sources": sources,
        "python": platform.python_version(),
        "llamaCppPython": importlib.metadata.version("llama-cpp-python"),
        "dependencies": dependencies,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Built and smoke-tested {target} worker: {executable}")


if __name__ == "__main__":
    main()
