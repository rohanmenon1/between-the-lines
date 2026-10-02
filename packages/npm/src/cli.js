#!/usr/bin/env node

import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const DEFAULT_SPACE = "coolbeanz79/between-the-lines";
const MODEL_LABELS = {
  base: "Base Mellum2 (richer)",
  tuned: "Fine-tuned LoRA (concise)"
};

function usage() {
  return `between-the-lines <file.py|file.java> [options]

Options:
  --language python|java    Override language detection from the extension
  --model base|tuned       Comment model to use. Default: base
  --output <file>          Write annotated code to a specific file
  --in-place               Replace the input file after validation
  --summary                Print summary and validation status to stderr
  --space <repo-id|url>    Hosted Gradio Space. Default: ${DEFAULT_SPACE}
  -h, --help               Show this help
`;
}

function parseArgs(argv) {
  const args = {
    input: "",
    language: "",
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
    } else if (arg === "--language") {
      args.language = argv[++index] ?? "";
    } else if (arg === "--output" || arg === "-o") {
      const outputPath = argv[++index] ?? "";
      if (!outputPath || outputPath.startsWith("-")) {
        throw new Error(`${arg} requires a file path`);
      }
      args.output = outputPath;
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
  args.language ||= ({ ".py": "python", ".java": "java" })[path.extname(args.input).toLowerCase()];
  if (!["python", "java"].includes(args.language)) {
    throw new Error("language must be python or java (use --language for files without a .py or .java extension)");
  }
  if (args.language === "java" && args.model === "tuned") {
    throw new Error("the tuned LoRA model supports Python only; use --model base for Java");
  }
  if (!args.space) {
    throw new Error("--space requires a repository ID or URL");
  }
  if (args.output && args.inPlace) {
    throw new Error("use either --output or --in-place, not both");
  }
  return args;
}

function defaultOutputPath(inputPath, language) {
  const parsed = path.parse(inputPath);
  const extension = language === "java" ? ".java" : ".py";
  return path.join(parsed.dir, `${parsed.name}.annotated${extension}`);
}

async function predict(client, source, modelLabel, language) {
  let multilangError;
  try {
    return await client.predict("/annotate_multilang", [source, modelLabel, language]);
  } catch (arrayError) {
    multilangError = arrayError;
    try {
      return await client.predict("/annotate_multilang", {
        source,
        model_label: modelLabel,
        language_label: language
      });
    } catch {
      if (language !== "python") {
        throw multilangError;
      }
    }
  }

  try {
    return await client.predict("/annotate", [source, modelLabel]);
  } catch (arrayError) {
    try {
      return await client.predict("/annotate", { source, model_label: modelLabel });
    } catch {
      throw multilangError ?? arrayError;
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
  const result = await predict(client, source, MODEL_LABELS[args.model], args.language === "java" ? "Java" : "Python");
  const [summary, annotated, status] = result.data;

  if (args.summary) {
    if (summary) {
      console.error(summary);
    }
    console.error(status);
  }

  if (!String(status).startsWith("Validated:") && !String(status).startsWith("Parsed successfully.")) {
    if (!args.summary) {
      console.error(status);
    }
    return 1;
  }

  if (args.inPlace) {
    await writeFile(args.input, annotated, "utf8");
  } else if (args.output) {
    await writeFile(args.output, annotated, "utf8");
  } else {
    const outputPath = defaultOutputPath(args.input, args.language);
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
