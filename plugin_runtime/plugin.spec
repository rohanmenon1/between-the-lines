"""Freeze only local capture, parsing and MCP tools; inference stays in Codex."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


root = Path(SPECPATH).parent
binaries, datas, hiddenimports = [], [], []
for package in ("tree_sitter", "tree_sitter_java"):
    binaries += collect_dynamic_libs(package)
    datas += collect_data_files(package)
    hiddenimports += collect_submodules(package)

analysis = Analysis(
    [str(root / "build" / "codex-plugin" / "plugin_entry.py")],
    pathex=[str(root / "plugin_runtime"), str(root / "worker")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["llama_cpp", "numpy", "torch", "transformers", "tkinter", "pytest",
              "btl_agent.backend", "btl_agent.providers", "btl_agent.agent",
              "btl_agent.__main__", "btl_agent.automatic"],
    noarchive=False,
)
python = PYZ(analysis.pure)
executable = EXE(
    python, analysis.scripts, [], exclude_binaries=True,
    name="between-the-lines-plugin", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True,
)
distribution = COLLECT(
    executable, analysis.binaries, analysis.datas,
    strip=False, upx=False, name="between-the-lines-plugin",
)
