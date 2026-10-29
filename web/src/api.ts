import type {
  EngineInfo,
  Job,
  Language,
  Project,
  ProjectSummary,
  Script,
  SfxItem,
} from "./types";

const BASE = "/api";

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

export const api = {
  listProjects: () => request<ProjectSummary[]>("/projects"),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (input: {
    title: string;
    language: Language;
    script_text: string;
  }) =>
    request<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateProject: (
    id: string,
    input: { title?: string; language?: Language; raw_script?: string },
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
      qwen_speaker?: string | null;
      ref_text?: string | null;
      ref_instruction?: string | null;
      baseline_instruct?: string;
    },
  ) =>
    request<Project>(`/projects/${projectId}/characters/${speaker}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  qwenVoices: () => request<string[]>("/engines/qwen3/voices"),
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
    request<{ project: Project; job: Job }>(
      `/projects/${projectId}/stems/${stemId}/take`,
      { method: "PATCH", body: JSON.stringify({ take_id: takeId }) },
    ),
  renderMix: (projectId: string) =>
    request<Job>(`/projects/${projectId}/mix`, { method: "POST" }),
  fileUrl: (projectId: string, path: string) =>
    `${BASE}/projects/${projectId}/files/${path}`,
  designReference: (
    projectId: string,
    speaker: string,
    input: {
      ref_text?: string;
      ref_instruction?: string;
      cfg_scale?: number;
      seed?: number;
    },
  ) =>
    request<Job>(`/projects/${projectId}/characters/${speaker}/reference`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  renderStem: (
    projectId: string,
    stemId: string,
    input: { candidates?: number; cfg_scale?: number; seed?: number },
  ) =>
    request<Job>(`/projects/${projectId}/stems/${stemId}/render`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  getJob: (jobId: string) => request<Job>(`/jobs/${jobId}`),
  projectJobs: (projectId: string) =>
    request<Job[]>(`/projects/${projectId}/jobs`),
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
