# between-the-lines-cli

Hosted CLI client for [between-the-lines](https://huggingface.co/spaces/coolbeanz79/between-the-lines).

Run without installing:

```bash
npx between-the-lines-cli path/to/file.py --model base --summary
npx between-the-lines-cli path/to/File.java --model base --summary
```

By default, the CLI creates a sibling file next to the input:

```text
path/to/file.annotated.py
path/to/File.annotated.java
```

Install globally if you prefer a reusable command:

```bash
npm install -g between-the-lines-cli
between-the-lines path/to/file.py --model base
```

Choose an exact output path or replace the input file:

```bash
npx between-the-lines-cli path/to/file.py --model base --output annotated.py
npx between-the-lines-cli path/to/file.py --model tuned --in-place
```

The CLI detects Python or Java from the file extension and sends the contents to the hosted Hugging Face Space. It writes output only after validation succeeds. The tuned LoRA model supports Python; use the base model for Java.

## Options

```text
--language python|java    Override language detection from the extension
--model base|tuned       Comment model to use. Default: base
--output <file>          Write annotated code to a specific file
--in-place               Replace the input file after validation
--summary                Print summary and validation status to stderr
--space <repo-id|url>    Override the hosted Gradio Space
```
