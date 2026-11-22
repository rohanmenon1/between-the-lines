// Development helper. Installed extensions use the same verified downloader.
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { downloadModel, validateManifest } = require("../dist/download");

const root = join(__dirname, "..");
const model = validateManifest(JSON.parse(readFileSync(join(root, "config/model.json"), "utf8")));
let last = -1;
downloadModel(model, join(root, "models", model.filename), {
  onProgress(received, total) {
    const percent = Math.floor(received * 100 / total);
    if (percent >= last + 10 || percent === 100) {
      console.log(`Local model: ${percent}%`);
      last = percent;
    }
  },
}).then(path => console.log(`Verified model: ${path}`)).catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
