# Marketplace upload checklist

The owner publishes the files. No upload or account change is part of local preparation.

## Current Windows beta

0.4.2 targets **Windows x64** and local trusted Python/Java projects. Its manifest is marked Preview. Use the final report, manifest and checksums in `artifacts/release-0.4.2/`; they identify the package and completed checks. The earlier 0.4.1 PowerShell candidate must not be uploaded. These checks use controlled events and do not establish new model accuracy or a fresh full IDE result.

The configured publisher is `between-the-lines-dev`. Ownership and Marketplace name availability have not been verified. Local packaging can validate the technical package without proving control of that publisher account. Confirm the exact owned publisher ID before freezing release files. If it differs, update `package.json` and rebuild/recheck the package; the publisher is part of the extension identity.

## Prepare reviewable files

1. Retain the passing native packaged-hook result and verify the final VSIX against it. Windows Codex hooks invoke the quoted executable directly, without PowerShell. Legacy or mismatched hooks require explicit reconnection. Windows Copilot capture is disabled; users can enter task context manually for other tools. Hook paths containing `%`, `!`, double quotes or control characters are rejected in this beta. Do not infer a full IDE or model-quality result from the native check.
2. Confirm the publisher ID and extension name. Keep `0.4.2` only if that version is still available for this identity. Use the Preview flag for this experimental beta; a Marketplace pre-release channel is a separate packaging choice.
3. Build the matching frozen worker and package into a **new** directory. Example:

   ~~~bat
   .\.venv\Scripts\python.exe scripts/build_worker.py
   .\.venv\Scripts\python.exe scripts/package_extension.py --target win32-x64 --output artifacts/release-0.4.2/between-the-lines-0.4.2-win32-x64.vsix
   .\.venv\Scripts\python.exe scripts/package_extension_source.py --output artifacts/release-0.4.2/between-the-lines-0.4.2-source.zip
   ~~~

4. Verify the **final VSIX bytes**, including the real Windows hooks, identity, `TargetPlatform="win32-x64"`, Preview metadata, worker, model manifest and bundled licenses. Record its SHA-256 and the matching source ZIP SHA-256 in a new release manifest alongside the checks used. Both packagers refuse existing output paths; keep prior artifacts intact.
5. Check the packaged README. It uses same-page anchors and includes privacy and license information. The package has no public repository URL; `--allow-missing-repository --no-rewrite-relative-links` does not host linked documentation. Source document paths are plain text until a real public source URL exists. Repository, homepage, support URL and a Marketplace PNG icon are optional listing improvements; do not invent them.

The upload directory is `artifacts/release-0.4.2/`, containing `between-the-lines-0.4.2-win32-x64.vsix`, `between-the-lines-0.4.2-source.zip`, `verification.json`, `release-0.4.2-manifest.json`, `SHA256SUMS.txt` and `README.md`. Confirm the VSIX hash matches its verification report and checksum file before uploading.

The source ZIP excludes binaries, local history, model weights, credentials and test artifacts. Its `SOURCE-MANIFEST.json` lists the included source bytes and hashes. It is a rebuildable source snapshot, not an extension or the separate Codex plugin install bundle.

## Owner upload

After final-file verification and publisher identity are resolved, sign in to [Manage Publishers & Extensions](https://marketplace.visualstudio.com/manage). Select the owned publisher, create a new Visual Studio Code extension (or update the existing identity), and choose the reviewed Windows VSIX. Review the listing and platform scope before publishing. The source ZIP and local verification reports are companion release files; they are not VSIX upload inputs. Retain the exact uploaded VSIX and checksum.

Official references: [required manifest fields](https://code.visualstudio.com/api/references/extension-manifest), [publisher and packaging guidance](https://code.visualstudio.com/api/working-with-extensions/publishing-extension), and [platform-specific packages](https://code.visualstudio.com/api/working-with-extensions/publishing-extension#platformspecific-extensions).
