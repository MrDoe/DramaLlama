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
 * Env:    BETTERSUNO_MCP_DIR (default: require.resolve("bettersuno-mcp"),
 *           i.e. the third_party/bettersuno-mcp subproject via
 *           suno-bridge/node_modules; falls back to ~/code/bettersuno-mcp)
 *         SUNO_BRIDGE_PORT   (default 9430)
 */
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
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
  const personas = await load("src/tools/personas.js");
  const allTools = [];
  generation.registerGenerationTools(null, allTools);
  library.registerLibraryTools(null, allTools);
  downloads.registerDownloadTools(null, allTools);
  personas.registerPersonaTools(null, allTools);
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

function slimPersona(item) {
  const vox = item?.is_vox_persona === true || item?.persona_type === "vox";
  return {
    id: item?.id ?? item?.persona_id ?? item?.uuid ?? null,
    name: item?.name ?? item?.title ?? "Unnamed voice",
    description: item?.description ?? "",
    // Exact persona_model values create_song expects.
    persona_model: vox ? "voice_persona" : "style_persona",
    image_url: item?.image_s3_id ?? item?.image_url ?? item?.avatar_url ?? null,
    // Root clip for voice preview via GET /clip/:id.
    root_clip_id: item?.root_clip_id ?? null,
  };
}

function personaList(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.personas)) return data.personas;
  if (Array.isArray(data?.data)) return data.data;
  return [];
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
      ...(body.persona_id ? { persona_id: body.persona_id } : {}),
      ...(body.persona_model ? { persona_model: body.persona_model } : {}),
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

      if (req.method === "GET" && url.pathname === "/personas") {
        try {
          // Own personas paginate (20/page via continuation_token); the
          // list_personas tool takes no args, so page sunoClient directly.
          const ownRaw = [];
          let continuation = null;
          for (let page = 0; page < 10; page++) {
            const params = continuation ? { continuation_token: continuation } : {};
            const result = await sunoClient.GET("/api/persona/get-personas/", { params });
            if (!result.ok) throw new Error(result.error || "Failed to fetch personas");
            ownRaw.push(...personaList(result.data));
            continuation = result.data?.continuation_token || null;
            if (!continuation) break;
          }
          const [followed, loved] = await Promise.all([
            callTool("list_followed_personas", {}),
            callTool("list_loved_personas", { page: 1 }),
          ]);
          // Loved/own overlap; dedupe by id, own wins.
          const seen = new Set(ownRaw.map((item) => item?.id));
          const lovedRaw = personaList(loved.data).filter((item) => {
            if (!item?.id || seen.has(item.id)) return false;
            seen.add(item.id);
            return true;
          });
          return send(res, 200, {
            ok: true,
            own: ownRaw.map(slimPersona),
            followed: personaList(followed.data).map(slimPersona),
            loved: lovedRaw.map(slimPersona),
          });
        } catch (error) {
          return send(res, 502, { ok: false, error: error.message });
        }
      }

      if (req.method === "GET" && url.pathname.startsWith("/personas/")) {
        const personaId = decodeURIComponent(url.pathname.slice("/personas/".length));
        if (!personaId) return send(res, 400, { ok: false, error: "persona_id required" });
        try {
          const result = await callTool("get_persona", { persona_id: personaId });
          const persona = result.data ?? {};
          const clip = persona.clip ?? null;
          return send(res, 200, {
            ok: true,
            // Slim: the raw payload embeds up to 100 full persona_clips (~800KB).
            persona: {
              ...slimPersona(persona),
              clip: clip
                ? {
                    id: clip.id ?? null,
                    status: clip.status ?? null,
                    audio_url: clip.audio_url ?? null,
                    video_url: clip.video_url ?? null,
                    media_urls: Array.isArray(clip.media_urls) ? clip.media_urls : [],
                    duration: clip.duration ?? clip.metadata?.duration ?? null,
                    model_name: clip.model_name ?? null,
                  }
                : null,
            },
          });
        } catch (error) {
          return send(res, 502, { ok: false, error: error.message });
        }
      }

      // Streamable mp3 preview of a voice: downloads (+decrypts when needed)
      // via the existing tool into a temp dir, transcodes on the fly, and
      // deletes everything. Nothing is persisted. Firefox cannot demux Suno's
      // m4a-opus catalog streams, hence the mp3 transcode.
      if (req.method === "GET" && url.pathname.startsWith("/preview/")) {
        const clipId = decodeURIComponent(url.pathname.slice("/preview/".length));
        if (!clipId) return send(res, 400, { ok: false, error: "clip_id required" });
        const tmpdir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "drama-preview-"));
        try {
          await callTool("download_song", {
            clip_id: clipId,
            format: "m4a",
            output_dir: tmpdir,
          });
          const files = (await fs.promises.readdir(tmpdir)).filter(
            (name) => !name.startsWith("."),
          );
          if (!files.length) throw new Error("bridge produced no preview file");
          const file = path.join(tmpdir, files[0]);
          res.writeHead(200, {
            "Content-Type": "audio/mpeg",
            "Cache-Control": "no-store",
          });
          await new Promise((resolve, reject) => {
            const ff = spawn("ffmpeg", [
              "-hide_banner",
              "-loglevel",
              "error",
              "-i",
              file,
              "-vn",
              "-ac",
              "2",
              "-ar",
              "44100",
              "-f",
              "mp3",
              "pipe:1",
            ]);
            ff.stdout.pipe(res, { end: true });
            ff.on("error", reject);
            ff.on("close", (code) =>
              code === 0 ? resolve() : reject(new Error(`ffmpeg exited ${code}`)),
            );
            res.on("close", () => {
              try {
                ff.kill();
              } catch {
                // already gone
              }
              resolve();
            });
          });
        } catch (error) {
          if (!res.headersSent) {
            return send(res, 502, { ok: false, error: error.message });
          }
          try {
            res.destroy();
          } catch {
            // already gone
          }
        } finally {
          await fs.promises.rm(tmpdir, { recursive: true, force: true });
        }
        return;
      }

      if (req.method === "GET" && url.pathname.startsWith("/clip/")) {
        const clipId = decodeURIComponent(url.pathname.slice("/clip/".length));
        if (!clipId) return send(res, 400, { ok: false, error: "clip_id required" });
        const result = await callTool("get_song", { clip_id: clipId });
        return send(res, 200, { ok: true, clip: result.data, text: result.text });
      }

      // Recent library clips for the Drama Llama "import from Suno" picker:
      // the newest own generations, so failed renders can be recovered
      // without generating again.
      if (req.method === "GET" && url.pathname === "/library") {
        const limit = Math.max(
          1,
          Math.min(500, Number(url.searchParams.get("limit")) || 50),
        );
        const stemsOnly =
          url.searchParams.get("stems_only") === "true" ||
          url.searchParams.get("stemsOnly") === "true" ||
          url.searchParams.get("stems") === "true";
        try {
          const args = { limit };
          if (stemsOnly) args.stems_only = true;
          const result = await callTool("list_library", args);
          const clips = extractClips(result.data).map(slimClip).slice(0, limit);
          return send(res, 200, { ok: true, clips });
        } catch (error) {
          return send(res, 502, { ok: false, error: error.message });
        }
      }

      // Force the BetterSuno extension to render its Turnstile widget in the
      // Suno tab and hand back a token. Used when a generation attempt failed
      // with "could not be solved automatically": the extension reuses Suno's
      // own widget token when possible and otherwise shows the checkbox.
      if (req.method === "POST" && url.pathname === "/captcha") {
        if (!bridge.getToken()) {
          return send(res, 503, {
            ok: false,
            error:
              "BetterSuno extension is not connected. Open suno.com in the BetterSuno browser and retry.",
          });
        }
        try {
          const token = await bridge.requestCaptcha(2); // 2 = Turnstile
          return send(res, 200, { ok: true, token });
        } catch (error) {
          return send(res, 502, { ok: false, error: error.message });
        }
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
