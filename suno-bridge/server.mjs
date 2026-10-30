#!/usr/bin/env node
/**
 * Local HTTP bridge between Drama Llama and the BetterSuno MCP modules.
 *
 * Why a sidecar: bettersuno-mcp is a stdio MCP server whose Suno client needs
 * the BetterSuno browser extension (Clerk token + Turnstile captcha relay) over
 * ws://127.0.0.1:9423. Drama Llama is Python, so it talks to this small Node
 * process over HTTP instead of reimplementing the Suno auth/captcha flow.
 *
 * Start:  node suno-bridge/server.mjs
 * Env:    BETTERSUNO_MCP_DIR (default ~/code/bettersuno-mcp)
 *         SUNO_BRIDGE_PORT   (default 9430)
 */
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const PORT = Number(process.env.SUNO_BRIDGE_PORT || 9430);
const MAX_BODY = 1024 * 1024;

function resolveBettersunoRoot() {
  if (process.env.BETTERSUNO_MCP_DIR) return process.env.BETTERSUNO_MCP_DIR;
  try {
    return path.dirname(require.resolve("bettersuno-mcp/package.json"));
  } catch {
    return path.join(os.homedir(), "code", "bettersuno-mcp");
  }
}

const ROOT = resolveBettersunoRoot();
const load = (relative) => import(pathToFileURL(path.join(ROOT, relative)).href);

let bridge;
let sunoClient;
const tools = new Map();

try {
  bridge = await load("src/ws-bridge.js");
  bridge.initBridge();
  ({ sunoClient } = await load("src/suno-client.js"));

  const generation = await load("src/tools/generation.js");
  const library = await load("src/tools/library.js");
  const downloads = await load("src/tools/downloads.js");
  const allTools = [];
  generation.registerGenerationTools(null, allTools);
  library.registerLibraryTools(null, allTools);
  downloads.registerDownloadTools(null, allTools);
  for (const tool of allTools) tools.set(tool.name, tool);
} catch (error) {
  console.error("[suno-bridge] failed to load bettersuno-mcp modules:", error);
  process.exit(1);
}

function send(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(payload),
    "Cache-Control": "no-store",
  });
  res.end(payload);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        reject(new Error("request body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      if (!chunks.length) return resolve({});
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch (error) {
        reject(new Error(`invalid JSON: ${error.message}`));
      }
    });
    req.on("error", reject);
  });
}

function toolText(result) {
  const item = Array.isArray(result?.content)
    ? result.content.find((entry) => entry?.type === "text")
    : null;
  return item?.text ?? "";
}

async function callTool(name, args) {
  const tool = tools.get(name);
  if (!tool) throw new Error(`unknown BetterSuno tool: ${name}`);
  const result = await tool.handler(args);
  const text = toolText(result);
  try {
    return { data: JSON.parse(text), text };
  } catch {
    return { data: null, text };
  }
}

function extractClips(data) {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== "object") return [];
  for (const key of ["clips", "data", "results", "songs"]) {
    const value = data[key];
    if (Array.isArray(value)) return value;
    if (value && Array.isArray(value.clips)) return value.clips;
  }
  return data.id ? [data] : [];
}

function slimClip(clip) {
  return {
    id: clip?.id ?? null,
    title: clip?.title ?? "",
    status: clip?.status ?? null,
    audio_url: clip?.audio_url ?? null,
    stream_audio_url: clip?.stream_audio_url ?? null,
    media_urls: Array.isArray(clip?.media_urls) ? clip.media_urls : [],
    duration: clip?.duration ?? clip?.metadata?.duration ?? null,
    model_name: clip?.model_name ?? null,
    created_at: clip?.created_at ?? null,
  };
}

async function generate(body) {
  const kind = body.kind === "song" ? "song" : "instrumental";
  const common = {
    title: body.title || "",
    tags: body.tags || "",
    negative_tags: body.negative_tags || "",
    duration: body.duration,
    mv: body.mv,
    weirdness: body.weirdness,
    style_weight: body.style_weight,
    audio_weight: body.audio_weight,
  };
  Object.keys(common).forEach((key) => {
    if (common[key] === undefined || common[key] === null || common[key] === "") {
      delete common[key];
    }
  });

  let result;
  if (kind === "song" && body.lyrics) {
    result = await callTool("create_song", {
      ...common,
      lyrics: body.lyrics,
      instrumental: Boolean(body.instrumental),
      vocal_gender: body.vocal_gender,
    });
  } else {
    result = await callTool("inspire_song", {
      ...common,
      description: body.prompt || body.description || "",
      instrumental: kind === "instrumental" ? true : Boolean(body.instrumental),
      vocal_gender: body.vocal_gender,
    });
  }
  const clips = extractClips(result.data).map(slimClip);
  return { ok: true, clips, raw: result.data };
}

async function main() {
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url || "/", "http://127.0.0.1");
      if (req.method === "GET" && url.pathname === "/health") {
        return send(res, 200, {
          ok: true,
          bridge: `ws://127.0.0.1:${process.env.MCP_WS_PORT || 9423}`,
          extensionConnected: Boolean(bridge.getToken()),
          bettersunoDir: ROOT,
          tools: tools.size,
        });
      }

      if (req.method === "GET" && url.pathname.startsWith("/clip/")) {
        const clipId = decodeURIComponent(url.pathname.slice("/clip/".length));
        if (!clipId) return send(res, 400, { ok: false, error: "clip_id required" });
        const result = await callTool("get_song", { clip_id: clipId });
        return send(res, 200, { ok: true, clip: result.data, text: result.text });
      }

      if (req.method === "POST" && url.pathname === "/generate") {
        const body = await readBody(req);
        if (!body.prompt && !body.lyrics && !body.description) {
          return send(res, 400, {
            ok: false,
            error: "prompt or lyrics is required",
          });
        }
        if (!bridge.getToken()) {
          return send(res, 503, {
            ok: false,
            error:
              "BetterSuno extension is not connected. Open suno.com in the BetterSuno browser and retry.",
          });
        }
        try {
          return send(res, 200, await generate(body));
        } catch (error) {
          return send(res, 502, { ok: false, error: error.message });
        }
      }

      if (req.method === "POST" && url.pathname === "/stems") {
        const body = await readBody(req);
        if (!body.clip_id) return send(res, 400, { ok: false, error: "clip_id required" });
        try {
          const result = await callTool("make_stems", { clip_id: body.clip_id });
          const clips = extractClips(result.data).map(slimClip);
          return send(res, 200, { ok: true, clips, raw: result.data, text: result.text });
        } catch (error) {
          return send(res, 502, { ok: false, error: error.message });
        }
      }

      if (req.method === "POST" && url.pathname === "/download") {
        const body = await readBody(req);
        if (!body.clip_id || !body.output_dir) {
          return send(res, 400, {
            ok: false,
            error: "clip_id and output_dir are required",
          });
        }
        try {
          const result = await callTool("download_song", {
            clip_id: body.clip_id,
            format: body.format || "m4a",
            output_dir: body.output_dir,
          });
          return send(res, 200, { ok: true, text: result.text });
        } catch (error) {
          return send(res, 502, { ok: false, error: error.message });
        }
      }

      return send(res, 404, { ok: false, error: "not found" });
    } catch (error) {
      return send(res, 500, { ok: false, error: error.message });
    }
  });

  server.listen(PORT, "127.0.0.1", () => {
    console.error(`[suno-bridge] listening on http://127.0.0.1:${PORT}`);
    console.error(`[suno-bridge] bettersuno-mcp modules: ${ROOT}`);
    console.error("[suno-bridge] waiting for the BetterSuno extension on ws://127.0.0.1:9423");
  });
}

main().catch((error) => {
  console.error("[suno-bridge] fatal:", error);
  process.exit(1);
});
