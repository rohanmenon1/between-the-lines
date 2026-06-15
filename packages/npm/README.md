# between-the-lines-cli

Hosted CLI client for [between-the-lines](https://huggingface.co/spaces/build-small-hackathon/between-the-lines).

Run without installing:

```bash
npx between-the-lines-cli path/to/file.py --model base --summary
```

By default, the CLI creates a sibling file next to the input:

```text
path/to/file.annotated.py
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

The CLI sends the file contents to the hosted Hugging Face Space and writes back the AST-validated annotated code.

## Options

```text
--model base|tuned       Comment model to use. Default: base
--output <file.py>       Write annotated code to a specific file
--in-place               Replace the input file after validation
--summary                Print summary and validation status to stderr
--space <repo-id|url>    Override the hosted Gradio Space
```
