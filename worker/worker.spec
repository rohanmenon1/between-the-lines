# Build with scripts/build_worker.py, which creates the absolute-import entrypoint.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).parent
binaries = collect_dynamic_libs("llama_cpp")
datas = collect_data_files("llama_cpp", includes=["lib/*", "*.json", "*.txt"])
hiddenimports = collect_submodules("llama_cpp", filter=lambda name: ".server" not in name)
for package in ("tree_sitter", "tree_sitter_java"):
    binaries += collect_dynamic_libs(package)
    datas += collect_data_files(package)
    hiddenimports += collect_submodules(package)
hiddenimports += ["btl_agent.capture"]

analysis = Analysis(
    [str(root / "build" / "worker_entry.py")],
    pathex=[str(root / "worker")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["tkinter", "pytest"],
    noarchive=False,
)
python = PYZ(analysis.pure)
executable = EXE(
    python, analysis.scripts, [], exclude_binaries=True,
    name="btl-worker", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True,
)
distribution = COLLECT(
    executable, analysis.binaries, analysis.datas,
    strip=False, upx=False, name="btl-worker",
)
