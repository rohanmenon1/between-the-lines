#!/usr/bin/env node

import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const DEFAULT_SPACE = "build-small-hackathon/between-the-lines";
const MODEL_LABELS = {
  base: "Base Mellum2 (richer)",
  tuned: "Fine-tuned LoRA (concise)"
};

function usage() {
  return `between-the-lines <file.py> [options]

Options:
  --model base|tuned       Comment model to use. Default: base
  --output <file.py>       Write annotated code to a specific file
  --in-place               Replace the input file after validation
  --summary                Print summary and validation status to stderr
  --space <repo-id|url>    Hosted Gradio Space. Default: ${DEFAULT_SPACE}
  -h, --help               Show this help
`;
}

function parseArgs(argv) {
  const args = {
    input: "",
    model: "base",
    output: "",
    inPlace: false,
    summary: false,
    space: DEFAULT_SPACE
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "-h" || arg === "--help") {
      args.help = true;
    } else if (arg === "--model") {
      args.model = argv[++index] ?? "";
    } else if (arg === "--output" || arg === "-o") {
      args.output = argv[++index] ?? "";
    } else if (arg === "--in-place") {
      args.inPlace = true;
    } else if (arg === "--summary") {
      args.summary = true;
    } else if (arg === "--space") {
      args.space = argv[++index] ?? "";
    } else if (!args.input) {
      args.input = arg;
    } else {
      throw new Error(`unexpected argument: ${arg}`);
    }
  }

  if (args.help) {
    return args;
  }
  if (!args.input) {
    throw new Error("missing input file");
  }
  if (!Object.hasOwn(MODEL_LABELS, args.model)) {
    throw new Error("--model must be 'base' or 'tuned'");
  }
  if (args.output && args.inPlace) {
    throw new Error("use either --output or --in-place, not both");
  }
  return args;
}

function defaultOutputPath(inputPath) {
  const parsed = path.parse(inputPath);
  return path.join(parsed.dir, `${parsed.name}.annotated${parsed.ext || ".py"}`);
}

async function predict(client, source, modelLabel) {
  try {
    return await client.predict("/annotate", [source, modelLabel]);
  } catch (arrayError) {
    try {
      return await client.predict("/annotate", {
        source,
        model_label: modelLabel
      });
    } catch {
      throw arrayError;
    }
  }
}

async function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(`error: ${error.message}\n`);
    console.error(usage());
    return 2;
  }

  if (args.help) {
    console.log(usage());
    return 0;
  }

  const source = await readFile(args.input, "utf8");
  const { Client } = await import("@gradio/client");
  const client = await Client.connect(args.space);
  const result = await predict(client, source, MODEL_LABELS[args.model]);
  const [summary, annotated, status] = result.data;

  if (args.summary) {
    if (summary) {
      console.error(summary);
    }
    console.error(status);
  }

  if (!String(status).startsWith("Validated:") && !String(status).startsWith("Parsed successfully.")) {
    console.error(status);
    return 1;
  }

  if (args.inPlace) {
    await writeFile(args.input, annotated, "utf8");
  } else if (args.output) {
    await writeFile(args.output, annotated, "utf8");
  } else {
    const outputPath = defaultOutputPath(args.input);
    await writeFile(outputPath, annotated, "utf8");
    console.error(`Wrote ${outputPath}`);
  }
  return 0;
}

main()
  .then((code) => {
    process.exitCode = code;
  })
  .catch((error) => {
    console.error(`error: ${error.message}`);
    process.exitCode = 1;
  });
