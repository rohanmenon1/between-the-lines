# between-the-lines 0.4.1 — archived candidate

**Unsuccessful candidate. Do not deploy or upload the earlier 0.4.1 package.** Its Windows PowerShell-hook workflow stalled during packaged verification. The separately versioned [0.4.2 Windows beta](release-0.4.2.md) uses a native Codex launcher and is the release being prepared.

The 0.4.1 candidate introduced endpoint/model-specific provider approval, UTF-8 byte-order-mark handling, original-context retention beyond 100 records and recorded requirements in manual analysis. Passing unit tests for those changes did not establish a working packaged Windows-hook flow.

Keep earlier 0.4.1 VSIX files and diagnostic logs intact as failed-candidate evidence. A later, separate native-launcher candidate still carrying 0.4.1 metadata passed controlled hooks; its report is `artifacts/native-candidate-bf5f44cd/verification.json`. That report does not retroactively validate the earlier stalled package. Final 0.4.2 artifacts require their own verification and checksums.

No new full IDE test or model inference was performed for these candidates. The 19 installed-editor checks and model measurements in [verification](verification.md) remain attributed to 0.4.0. No public upload or publisher ownership is established by the candidate files.
