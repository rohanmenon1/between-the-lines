import { createHash, randomUUID } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { mkdir, rename, rm, stat } from "node:fs/promises";
import * as http from "node:http";
import * as https from "node:https";
import { dirname } from "node:path";
import { Transform } from "node:stream";
import { pipeline } from "node:stream/promises";

export interface ModelManifest {
  name: string;
  filename: string;
  url: string;
  sha256: string;
  sizeBytes: number;
  revision: string;
  tuned: boolean;
}

export interface DownloadOptions {
  signal?: AbortSignal;
  onProgress?: (received: number, total: number) => void;
  /** Only local HTTP fixtures may opt out of HTTPS. Never used by the extension. */
  allowHttpForTests?: boolean;
}

export function validateManifest(value: unknown): ModelManifest {
  const item = value as Partial<ModelManifest> | null;
  if (!item || typeof item.name !== "string" || !item.name ||
      typeof item.filename !== "string" || !/^[\w.-]+\.gguf$/.test(item.filename) ||
      typeof item.url !== "string" || new URL(item.url).protocol !== "https:" ||
      typeof item.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(item.sha256) ||
      !Number.isSafeInteger(item.sizeBytes) || item.sizeBytes! <= 0 ||
      typeof item.revision !== "string" || !/^[a-f0-9]{40}$/.test(item.revision) ||
      typeof item.tuned !== "boolean") {
    throw new Error("The bundled model manifest is invalid.");
  }
  return item as ModelManifest;
}

function cancelled(): Error {
  const error = new Error("Model preparation cancelled.");
  error.name = "AbortError";
  return error;
}

export async function verifyModel(path: string, manifest: ModelManifest, signal?: AbortSignal): Promise<boolean> {
  if (signal?.aborted) throw cancelled();
  try {
    const info = await stat(path);
    if (!info.isFile() || info.size !== manifest.sizeBytes) return false;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
  const hash = createHash("sha256");
  const stream = createReadStream(path, { signal });
  for await (const chunk of stream) hash.update(chunk);
  return hash.digest("hex") === manifest.sha256;
}

function openDownload(url: URL, options: DownloadOptions, redirects = 0): Promise<http.IncomingMessage> {
  const localFixture = options.allowHttpForTests && url.protocol === "http:" &&
    ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !localFixture) return Promise.reject(new Error("Model downloads require HTTPS."));
  if (url.username || url.password) return Promise.reject(new Error("Credentials are not permitted in model download URLs."));
  if (redirects > 5) return Promise.reject(new Error("Too many model download redirects."));
  if (options.signal?.aborted) return Promise.reject(cancelled());
  return new Promise((resolve, reject) => {
    const client = url.protocol === "https:" ? https : http;
    const request = client.get(url, { signal: options.signal, headers: { "User-Agent": "between-the-lines/0.1" } }, response => {
      if ([301, 302, 303, 307, 308].includes(response.statusCode ?? 0)) {
        response.resume();
        if (!response.headers.location) return reject(new Error("Model redirect did not include a location."));
        try { openDownload(new URL(response.headers.location, url), options, redirects + 1).then(resolve, reject); }
        catch { reject(new Error("Model download returned an invalid redirect.")); }
        return;
      }
      if (response.statusCode !== 200) {
        response.resume();
        reject(new Error(`Model download failed (HTTP ${response.statusCode ?? "unknown"}).`));
        return;
      }
      resolve(response);
    });
    request.setTimeout(30_000, () => request.destroy(new Error("Model download stopped responding.")));
    request.once("error", reject);
  });
}

/** A partial or corrupt download can never replace a verified model. */
export async function downloadModel(manifest: ModelManifest, destination: string, options: DownloadOptions = {}): Promise<string> {
  if (await verifyModel(destination, manifest, options.signal)) {
    options.onProgress?.(manifest.sizeBytes, manifest.sizeBytes);
    return destination;
  }
  await mkdir(dirname(destination), { recursive: true });
  const temporary = `${destination}.${randomUUID()}.part`;
  const hash = createHash("sha256");
  let received = 0;
  try {
    options.onProgress?.(0, manifest.sizeBytes);
    const response = await openDownload(new URL(manifest.url), options);
    const declaredSize = response.headers["content-length"];
    if (declaredSize !== undefined && Number(declaredSize) !== manifest.sizeBytes) {
      response.destroy();
      throw new Error("Model download size differs from its pinned manifest.");
    }
    const measure = new Transform({
      transform(chunk: Buffer, _encoding, callback) {
        received += chunk.length;
        if (received > manifest.sizeBytes) return callback(new Error("Model download exceeds its expected size."));
        hash.update(chunk);
        options.onProgress?.(received, manifest.sizeBytes);
        callback(null, chunk);
      },
    });
    await pipeline(response, measure, createWriteStream(temporary, { flags: "wx" }), { signal: options.signal });
    if (received !== manifest.sizeBytes || hash.digest("hex") !== manifest.sha256) {
      throw new Error("Model verification failed. The incomplete download was removed.");
    }
    if (options.signal?.aborted) throw cancelled();
    // Windows rename cannot replace a corrupt cache entry. Remove it only once its replacement is verified.
    await rm(destination, { force: true });
    await rename(temporary, destination);
    return destination;
  } finally {
    await rm(temporary, { force: true });
  }
}
