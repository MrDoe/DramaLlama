import type {
  AlignmentResult,
  EngineInfo,
  EngineName,
  Job,
  Language,
  Project,
  ProjectSummary,
  Script,
  SfxItem,
  SunoLibraryClip,
  SunoPersona,
} from "./types";

const BASE = "/api";

export interface LlmConfig {
  model: string;
  provider: "local" | "cloud";
  api: "ollama" | "openai";
  url: string;
  apiKeyConfigured: boolean;
  sources: Record<string, "file" | "env" | "default">;
}

export interface LlmTestResult {
  ok: boolean;
  api: "ollama" | "openai";
  provider: "local" | "cloud";
  model: string;
  models: string[];
  excluded: string[];
  error: string | null;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export interface SunoPersonas {
  ok: boolean;
  own: SunoPersona[];
  followed: SunoPersona[];
  loved?: SunoPersona[];
}

// One shared personas request per page lifetime: every Suno voice picker
// (Cast panel, each Render stem card) reads from this instead of fetching.
let personasRequest: Promise<SunoPersonas> | null = null;

function sharedSunoPersonas(): Promise<SunoPersonas> {
  if (!personasRequest) {
    personasRequest = request<SunoPersonas>("/suno/personas").catch(
      (reason: unknown) => {
        personasRequest = null;
        throw reason;
      },
    );
  }
  return personasRequest;
}

export const api = {
  listProjects: () => request<ProjectSummary[]>("/projects"),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (input: {
    title: string;
    language: Language;
    script_text: string;
    default_engine?: EngineName | null;
  }) =>
    request<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateProject: (
    id: string,
    input: {
      title?: string;
      language?: Language;
      raw_script?: string;
      default_engine?: EngineName | "auto" | null;
    },
  ) =>
    request<Project>(`/projects/${id}`, {
      method: "PUT",
      body: JSON.stringify(input),
    }),
  deleteProject: (id: string) =>
    request<void>(`/projects/${id}`, { method: "DELETE" }),
  parseProject: (id: string, method: "deterministic" | "llm" = "deterministic") =>
    request<Project>(`/projects/${id}/parse`, {
      method: "POST",
      body: JSON.stringify({ method }),
    }),
  parseProjectStream: async (
    id: string,
    onToken: (chunk: string) => void,
  ): Promise<Project> => {
    const response = await fetch(`${BASE}/projects/${id}/parse/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ method: "llm" }),
    });
    if (!response.ok || !response.body) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch {
        // keep status text
      }
      throw new ApiError(response.status, detail);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result: Project | null = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const lines = part.split("\n");
        let event = "message";
        let data = "";
        for (const line of lines) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        try {
          const parsed = JSON.parse(data);
          if (event === "token") onToken(parsed as string);
          else if (event === "result") result = parsed as Project;
          else if (event === "error") throw new Error(String(parsed));
        } catch (err) {
          if (err instanceof Error && event === "error") throw err;
        }
      }
    }
    if (!result) throw new Error("LLM parse did not return a result");
    return result;
  },
  buildStems: (id: string) =>
    request<Project>(`/projects/${id}/stems`, {
      method: "POST",
    }),
  updateCharacter: (
    projectId: string,
    speaker: string,
    input: {
      name?: string;
      description?: string;
      ref_text?: string | null;
      ref_instruction?: string | null;
      baseline_instruct?: string;
      suno_persona_id?: string | null;
      suno_persona_model?: string | null;
      suno_mv?: string;
    },
  ) =>
    request<Project>(`/projects/${projectId}/characters/${speaker}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  searchSfx: (query = "", limit = 40) =>
    request<SfxItem[]>(`/sfx?q=${encodeURIComponent(query)}&limit=${limit}`),
  scanSfx: () => request<{ indexed: number }>("/sfx/scan", { method: "POST" }),
  sfxAudioUrl: (id: string) => `${BASE}/sfx/${id}/audio`,
  addCue: (
    projectId: string,
    cue: {
      kind: string;
      sfx_id: string;
      name: string;
      anchor_line_id: string;
      offset_s?: number;
      gain_db?: number;
      fade_in_s?: number;
      fade_out_s?: number;
      loop?: boolean;
      duck?: boolean;
      filter?: string;
    },
  ) =>
    request<Project>(`/projects/${projectId}/cues`, {
      method: "POST",
      body: JSON.stringify(cue),
    }),
  deleteCue: (projectId: string, cueId: string) =>
    request<void>(`/projects/${projectId}/cues/${cueId}`, { method: "DELETE" }),
  selectTake: (projectId: string, stemId: string, takeId: string) =>
    request<{ project: Project; job: Job | null }>(
      `/projects/${projectId}/stems/${stemId}/take`,
      { method: "PATCH", body: JSON.stringify({ take_id: takeId }) },
    ),
  updateAlignment: (
    projectId: string,
    stemId: string,
    cuts: {
      line_id: string;
      take_id?: string | null;
      start_s: number;
      end_s: number;
    }[],
    rebuildMix = true,
  ) =>
    request<{ project: Project; alignment: AlignmentResult }>(
      `/projects/${projectId}/stems/${stemId}/alignment`,
      {
        method: "PATCH",
        body: JSON.stringify({ cuts, rebuild_mix: rebuildMix }),
      },
    ),
  updateAlignmentsBatch: (
    projectId: string,
    stems: Record<
      string,
      { line_id: string; take_id?: string | null; start_s: number; end_s: number }[]
    >,
    rebuildMix = true,
  ) =>
    request<{ project: Project; alignments: Record<string, AlignmentResult> }>(
      `/projects/${projectId}/alignments`,
      {
        method: "PATCH",
        body: JSON.stringify({ stems, rebuild_mix: rebuildMix }),
      },
    ),
  sunoStatus: () =>
    request<{
      ok: boolean;
      extensionConnected?: boolean;
      error?: string;
      bridge?: string;
    }>("/suno/status"),
  sunoPersonas: () => sharedSunoPersonas(),
  sunoPersonaClip: (personaId: string) =>
    request<{ ok: boolean; persona: Record<string, unknown> }>(
      `/suno/personas/${encodeURIComponent(personaId)}`,
    ),
  sunoPreviewUrl: (clipId: string) =>
    `${BASE}/suno/preview/${encodeURIComponent(clipId)}`,
  requestSunoCaptcha: () =>
    request<{ ok: boolean }>("/suno/captcha", { method: "POST" }),
  generateSuno: (
    projectId: string,
    input: {
      kind: string;
      prompt: string;
      title?: string;
      tags?: string;
      duration?: number;
      lyrics?: string;
      vocal_gender?: string;
    },
  ) =>
    request<Job>(`/projects/${projectId}/suno/generate`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  renderMix: (projectId: string) =>
    request<Job>(`/projects/${projectId}/mix`, { method: "POST" }),
  fileUrl: (projectId: string, path: string) =>
    `${BASE}/projects/${projectId}/files/${path}`,
  uploadVoiceSample: async (
    projectId: string,
    speaker: string,
    file: File,
    name?: string,
  ) => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    const response = await fetch(
      `${BASE}/projects/${projectId}/characters/${encodeURIComponent(speaker)}/voice-sample`,
      { method: "POST", body: form },
    );
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch {
        // keep status text
      }
      throw new ApiError(response.status, detail);
    }
    return (await response.json()) as Project;
  },
  designReference: (
    projectId: string,
    speaker: string,
    input: {
      ref_text?: string;
      ref_instruction?: string;
      cfg_scale?: number;
      seed?: number;
      name?: string;
    },
  ) =>
    request<Job>(`/projects/${projectId}/characters/${speaker}/reference`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  renderStem: (
    projectId: string,
    stemId: string,
    input: {
      candidates?: number;
      cfg_scale?: number;
      seed?: number;
      ref_voice_id?: string | null;
      suno_weirdness?: number | null;
      suno_style_weight?: number | null;
      suno_audio_weight?: number | null;
      suno_mv?: string | null;
      suno_vocal_gender?: string | null;
    },
  ) =>
    request<Job>(`/projects/${projectId}/stems/${stemId}/render`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  extractStem: (
    projectId: string,
    stemId: string,
    input: { take_id?: string; method?: "demucs" | "suno"; align?: boolean },
  ) =>
    request<Job>(`/projects/${projectId}/stems/${stemId}/extract`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  importSunoTake: (projectId: string, stemId: string, clipId: string) =>
    request<Job>(`/projects/${projectId}/stems/${stemId}/import-suno`, {
      method: "POST",
      body: JSON.stringify({ clip_id: clipId }),
    }),
  sunoLibrary: (limit = 50) =>
    request<{ ok: boolean; clips: SunoLibraryClip[] }>(
      `/suno/library?limit=${limit}`,
    ),
  updateStem: (
    projectId: string,
    stemId: string,
    input: {
      cfg_scale?: number;
      seed?: number;
      suno_lyrics?: string | null;
      suno_title?: string | null;
      suno_tags?: string | null;
      suno_weirdness?: number | null;
      suno_style_weight?: number | null;
      suno_audio_weight?: number | null;
      suno_mv?: string | null;
      suno_vocal_gender?: string | null;
      ref_voice_id?: string | null;
    },
  ) =>
    request<Project>(`/projects/${projectId}/stems/${stemId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  suggestSunoStyle: (
    projectId: string,
    stemId: string,
    input?: { method?: "llm" | "deterministic"; overwrite?: boolean; only?: string[] },
  ) =>
    request<Project>(`/projects/${projectId}/stems/${stemId}/suno-style`, {
      method: "POST",
      body: JSON.stringify(input ?? { method: "llm" }),
    }),
  getJob: (jobId: string) => request<Job>(`/jobs/${jobId}`),
  projectJobs: (projectId: string) =>
    request<Job[]>(`/projects/${projectId}/jobs`),
  getConfig: () =>
    request<{ llm: LlmConfig; suno?: { plan: "free" | "pro"; sources: Record<string, string> } }>(
      "/config",
    ),
  updateConfig: (
    llm: {
      api?: string;
      url?: string;
      model?: string;
      api_key?: string | null;
    } | null,
    suno?: { plan: "free" | "pro" },
  ) =>
    request<{ llm: LlmConfig; suno?: { plan: "free" | "pro" } }>("/config", {
      method: "PUT",
      body: JSON.stringify({ ...(llm ? { llm } : {}), ...(suno ? { suno } : {}) }),
    }),
  testConfig: (llm?: {
    api?: string;
    url?: string;
    model?: string;
    api_key?: string | null;
  }) =>
    request<LlmTestResult>("/config/test", {
      method: "POST",
      body: JSON.stringify(llm ? { llm } : {}),
    }),
  watchJob: (jobId: string, onUpdate: (job: Job) => void) => {
    const source = new EventSource(`${BASE}/jobs/${jobId}/events`);
    source.addEventListener("job", (event) => {
      try {
        const job = JSON.parse((event as MessageEvent).data) as Job;
        onUpdate(job);
        if (["succeeded", "failed", "cancelled"].includes(job.status)) {
          source.close();
        }
      } catch {
        // ignore malformed event
      }
    });
    return () => source.close();
  },
  updateScript: (id: string, script: Script) =>
    request<Project>(`/projects/${id}/script`, {
      method: "PUT",
      body: JSON.stringify({ script }),
    }),
  engines: () => request<EngineInfo[]>("/engines"),
};

/** Resolve when a job reaches a terminal state (drives batch rendering). */
export function waitForJob(jobId: string): Promise<Job> {
  return new Promise((resolve, reject) => {
    const stop = api.watchJob(jobId, (job) => {
      if (["succeeded", "failed", "cancelled"].includes(job.status)) {
        stop();
        resolve(job);
      }
    });
    // The SSE stream can drop without a terminal event; poll as a safety net.
    const timer = setInterval(() => {
      api
        .getJob(jobId)
        .then((job) => {
          if (["succeeded", "failed", "cancelled"].includes(job.status)) {
            clearInterval(timer);
            stop();
            resolve(job);
          }
        })
        .catch(() => {
          /* keep waiting */
        });
    }, 5000);
    setTimeout(() => {
      clearInterval(timer);
      stop();
      reject(new Error("job did not finish in time"));
    }, 30 * 60 * 1000);
  });
}
