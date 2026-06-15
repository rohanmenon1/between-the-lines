# Data

Local data workspace for building the comment-generation SFT dataset.

Actual generated training files are intended to live in a persistent Modal
Volume named `between-the-lines-data`. Modal function containers are ephemeral,
so writing to a normal path inside a Modal run is not enough; generated JSONL
must be written under the mounted volume path and committed.

The intended processed format is JSONL with one training example per line:

```json
{"language":"python","comment_prefix":"# ","code":"def ...","prompt":"Write exactly one concise Python comment...","completion":"# ...","source":"code_search_net"}
```

Generated dataset files under `raw/`, `processed/`, and `eval/` are ignored by git except for `.gitkeep` placeholders.

Build on Modal from the repo root:

```powershell
$env:PYTHONUTF8='1'
py training\modal_truststore.py run training\modal_build_dataset.py --limit 10000 --eval-size 500
```

The Modal job writes:

- `/data/processed/codesearchnet_python_comments.jsonl`
- `/data/eval/codesearchnet_python_comments_eval.jsonl`
