export type Language = "en" | "de";
export type EngineName = "breeze" | "chatterbox" | "fake";

export interface Character {
  id: string;
  name: string;
  description: string;
  engine: EngineName;
  ref_text?: string | null;
  ref_instruction?: string | null;
  ref_file?: string | null;
  baseline_instruct: string;
  locked: boolean;
  f0_hz?: number | null;
}

export interface Line {
  id: string;
  scene: number;
  speaker: string;
  text: string;
  direction: string;
  parenthetical?: string | null;
  sfx: string[];
  emotion: string;
  intensity: number;
  source_index?: number | null;
}

export interface Scene {
  id: number;
  heading: string;
  setting: string;
  stage_directions: string[];
  lines: Line[];
}

export interface Script {
  title: string;
  language: Language;
  characters: Record<string, Character>;
  scenes: Scene[];
}

export interface ParseWarning {
  source_line: number;
  message: string;
  text: string;
}

export interface ParseReport {
  method: "deterministic" | "llm";
  source_lines: number;
  scene_headings: number;
  dialogue_lines: number;
  stage_directions: number;
  ignored_lines: number;
  unknown_lines: number;
  coverage: number;
  runtime_estimate_s: number;
  warnings: ParseWarning[];
}

export interface SfxItem {
  id: string;
  name: string;
  path: string;
  folder: string;
  duration_s: number;
  sample_rate: number;
  channels: number;
  size_bytes: number;
  mtime: number;
  tags: string[];
}

export interface Cue {
  id: string;
  kind: "bed" | "one_shot" | "music";
  sfx_id: string;
  name: string;
  anchor_line_id: string;
  offset_s: number;
  gain_db: number;
  fade_in_s: number;
  fade_out_s: number;
  loop: boolean;
  duck: boolean;
  filter: "none" | "telephone" | "radio";
}

export interface SunoTrack {
  clip_id: string;
  title: string;
  file: string;
  sfx_id: string;
  duration_s?: number | null;
  prompt: string;
  created_at: string;
}

export interface MixResult {
  file: string;
  mp3?: string | null;
  duration_s: number;
  loudness_target: string;
  line_count: number;
  cue_count: number;
  warnings: string[];
  created_at: string;
}

export interface ProjectSettings {
  pause_ms: number;
  scene_gap_ms: number;
  loudness_target: "podcast" | "broadcast";
  sfx_library_roots: string[];
  exclude_sfx_dirs: string[];
}

export interface Take {
  id: string;
  file: string;
  engine: string;
  duration_s: number;
  sample_rate: number;
  seed: number;
  f0_hz?: number | null;
  mfcc_sim?: number | null;
  selected: boolean;
  created_at: string;
}

export interface Stem {
  id: string;
  scene: number;
  speaker: string;
  line_ids: string[];
  text: string;
  instruction: string;
  engine?: string | null;
  cfg_scale: number;
  seed: number;
  takes: Take[];
}

export interface AlignedLine {
  line_id: string;
  start_s: number;
  end_s: number;
  confidence?: number | null;
  fallback: boolean;
}

export interface AlignmentResult {
  stem_id: string;
  lines: AlignedLine[];
  wer?: number | null;
  aligner: string;
  created_at: string;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Job {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  status: JobStatus;
  priority: number;
  progress: number;
  message: string;
  result?: Record<string, unknown> | null;
  error?: string | null;
  attempts: number;
  max_attempts: number;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface Project {
  id: string;
  title: string;
  language: Language;
  raw_script: string;
  script: Script;
  report: ParseReport | null;
  stems: Stem[];
  alignments: Record<string, AlignmentResult>;
  cues: Cue[];
  suno: SunoTrack[];
  mix?: MixResult | null;
  settings: ProjectSettings;
  created_at: string;
  updated_at: string;
}

export interface ProjectSummary {
  id: string;
  title: string;
  language: Language;
  scenes: number;
  lines: number;
  runtime_estimate_s: number;
  coverage: number;
  updated_at: string;
}

export interface EngineInfo {
  name: EngineName;
  languages: string[];
  sample_rate: number;
  supports_reference_clone: boolean;
  voice_mode: "reference" | "preset";
  description: string;
}
