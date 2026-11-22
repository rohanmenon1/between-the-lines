# Changelog

## 0.4.2 (Windows beta)

Native launcher checks passed during development; final 0.4.2 package verification and upload files are being prepared. Publisher ownership remains unverified. This version replaces the unsuccessful 0.4.1 candidate.

- Bind automatic API approval to the endpoint and model.
- Launch Windows Codex hooks through the quoted bundled executable without PowerShell.
- Require explicit reconnect for legacy or mismatched hook configurations.
- Disable unverified Windows Copilot capture and offer manual task context for other tools.
- Reject Windows hook paths containing `%`, `!`, double quotes or control characters.
- Preserve UTF-8 byte-order marks during verified annotation handling.
- Keep original requirements beyond 100 records visible to context budgeting.
- Include recorded file requirements in manual analysis.
- Validate worker freshness during packaging and provide a reproducible source archive.

## 0.4.1 (archived unsuccessful candidate)

The earlier Windows PowerShell-hook candidate stalled and must not be deployed. Later native-launcher development checks do not validate that artifact. Use the separately packaged 0.4.2 beta.

## 0.4.0

- Generate readable comments from captured tasks and changed Python or Java declarations.
- Keep original prompt evidence separate from generated wording.
- Add same-model support review, configurable providers and a recorded-context inspector.
