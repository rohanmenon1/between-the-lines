# Third-party software

The packaged local worker includes Python, llama-cpp-python and its native llama.cpp libraries, Tree-sitter and its Java grammar, NumPy, Jinja2, MarkupSafe, diskcache, and typing_extensions. PyInstaller supplies the executable bootloader and runtime hooks.

The build copies the exact license and notice files from the installed distributions into `worker/bin/btl-worker/third-party-licenses`. This includes NumPy's bundled dependency notices and PyInstaller's bootloader exception. `worker/bin/build-info.json` and the license directory's `index.json` list the versions and copied files for that build.

The native llama.cpp license is retained separately from the Python binding's license. It is included from the installed runtime when available, or from `worker/licenses/llama.cpp-LICENSE`.

The extension downloads model weights separately; they are not bundled in the VSIX. The selected default is [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B), published by Qwen under [Apache License 2.0](https://huggingface.co/Qwen/Qwen3.5-2B/blob/main/LICENSE). It is used for text inference without an app-specific fine-tune or LoRA adapter.

The GGUF quantization comes from [Unsloth's Qwen3.5-2B-GGUF repository](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF), which also identifies Apache 2.0 as its license. The manifest pins revision `f6d5376be1edb4d416d56da11e5397a961aca8ae`, file `Qwen3.5-2B-Q4_K_M.gguf`, 1,280,835,840 bytes, SHA-256 `aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223`. See `config/model.json` for the download URL.

Earlier development candidates, including Qwen2.5-Coder 0.5B, are not the current default. Their evaluation artifacts do not change the license or identity of the selected download.

Third-party components retain their respective licenses. The project's MIT license applies to its own code.
