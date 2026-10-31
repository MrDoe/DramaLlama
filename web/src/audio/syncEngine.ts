/**
 * Sample-accurate multitrack transport on WebAudio.
 *
 * One AudioBuffer per take, one GainNode per track (mute/solo/volume). Every
 * line clip is started as its own source at its global timeline slot, all
 * against the same context clock, so the preview matches the mix placement.
 */

export interface LoadedTrack {
  stemId: string;
  takeId: string;
  duration: number;
  peaks: number[];
}

/** One audible line clip on the global timeline. */
export interface ScheduleSegment {
  stemId: string;
  /** Which take of the stem this clip's cut window lives in. */
  takeId: string;
  /** Cut window inside the stem take. */
  localStart: number;
  localEnd: number;
  /** Global timeline slot. */
  start: number;
  end: number;
}

const PEAK_COUNT = 8000;

function computePeaks(buf: AudioBuffer): number[] {
  const ch = buf.getChannelData(0);
  const per = Math.max(1, Math.floor(ch.length / PEAK_COUNT));
  const peaks: number[] = [];
  for (let i = 0; i < ch.length; i += per) {
    let max = 0;
    for (let j = 0; j < per && i + j < ch.length; j++) {
      const v = Math.abs(ch[i + j]);
      if (v > max) max = v;
    }
    peaks.push(max);
  }
  return peaks;
}

export class SyncEngine {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private buffers = new Map<string, AudioBuffer>();
  private lastUsed = new Map<string, number>();
  private gains = new Map<string, GainNode>();
  private sources: AudioBufferSourceNode[] = [];
  private startCtx = 0;
  private startGlobal = 0;
  private frozen = 0;
  private _playing = false;
  private onlyStem: string | null = null;
  /** Cap decoded takes; the board can reference many generations. */
  private maxTracks = 12;

  muted = new Set<string>();
  solo = new Set<string>();
  volumes = new Map<string, number>();

  get playing(): boolean {
    return this._playing;
  }

  ensureCtx(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.master = this.ctx.createGain();
      this.master.connect(this.ctx.destination);
    }
    return this.ctx;
  }

  async resume(): Promise<void> {
    const ctx = this.ensureCtx();
    if (ctx.state === "suspended") {
      await ctx.resume();
    }
  }

  hasTrack(takeId: string): boolean {
    if (this.buffers.has(takeId)) {
      this.lastUsed.set(takeId, Date.now());
      return true;
    }
    return false;
  }

  getDuration(takeId: string): number {
    const buf = this.buffers.get(takeId);
    if (buf) this.lastUsed.set(takeId, Date.now());
    return buf?.duration ?? 0;
  }

  /** Drop decoded takes outside `keep` (called when the board's set shrinks). */
  prune(keep: Iterable<string>): void {
    const keepSet = new Set(keep);
    for (const id of [...this.buffers.keys()]) {
      if (!keepSet.has(id)) {
        this.buffers.delete(id);
        this.lastUsed.delete(id);
      }
    }
  }

  private evictIfNeeded(): void {
    while (this.buffers.size >= this.maxTracks) {
      let oldest: string | null = null;
      let oldestTime = Infinity;
      for (const [id, t] of this.lastUsed) {
        if (this.buffers.has(id) && t < oldestTime) {
          oldestTime = t;
          oldest = id;
        }
      }
      if (oldest === null) break;
      this.buffers.delete(oldest);
      this.lastUsed.delete(oldest);
    }
  }

  async loadTrack(
    stemId: string,
    takeId: string,
    url: string,
  ): Promise<LoadedTrack> {
    const cached = this.buffers.get(takeId);
    if (cached) {
      this.lastUsed.set(takeId, Date.now());
      return {
        stemId,
        takeId,
        duration: cached.duration,
        peaks: computePeaks(cached),
      };
    }
    const res = await fetch(url);
    if (!res.ok) throw new Error(`audio fetch failed: ${res.status}`);
    const arr = await res.arrayBuffer();
    const ctx = this.ensureCtx();
    const buf = await ctx.decodeAudioData(arr.slice(0));
    this.evictIfNeeded();
    this.buffers.set(takeId, buf);
    this.lastUsed.set(takeId, Date.now());
    if (!this.gains.has(stemId)) {
      const g = ctx.createGain();
      g.connect(this.master!);
      this.gains.set(stemId, g);
    }
    this.applyGains();
    return {
      stemId,
      takeId,
      duration: buf.duration,
      peaks: computePeaks(buf),
    };
  }

  unloadTrack(takeId: string): void {
    this.buffers.delete(takeId);
    this.lastUsed.delete(takeId);
  }

  private audible(stemId: string): boolean {
    if (this.onlyStem) return stemId === this.onlyStem;
    if (this.solo.size > 0) return this.solo.has(stemId);
    return !this.muted.has(stemId);
  }

  applyGains(): void {
    for (const [id, g] of this.gains) {
      const v = this.volumes.get(id) ?? 1;
      g.gain.value = this.audible(id) ? v : 0;
    }
  }

  /**
   * Start every scheduled line clip at global time `fromGlobal`. Clips that
   * already started are entered mid-window, clips further ahead are scheduled
   * on the same clock. With `onlyStemId`, audition just that track (mute/solo
   * ignored); the caller stops at the boundary via getTime().
   */
  play(fromGlobal: number, segments: ScheduleSegment[], opts?: { onlyStemId?: string }): void {
    const ctx = this.ensureCtx();
    this.stopSources();
    this.onlyStem = opts?.onlyStemId ?? null;
    this.applyGains();
    const t0 = ctx.currentTime + 0.03;
    let started = 0;

    for (const seg of segments) {
      if (this.onlyStem && seg.stemId !== this.onlyStem) continue;
      const gain = this.gains.get(seg.stemId);
      if (!gain || gain.gain.value <= 0) continue;
      const buf = this.buffers.get(seg.takeId);
      if (!buf) continue;
      this.lastUsed.set(seg.takeId, Date.now());

      const localEnd = Math.min(seg.localEnd, buf.duration);
      const localFrom = Math.max(0, seg.localStart) + Math.max(0, fromGlobal - seg.start);
      const remaining = localEnd - localFrom;
      if (remaining <= 0.005) continue;

      const when = t0 + Math.max(0, seg.start - fromGlobal);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(gain);
      src.start(when, localFrom, remaining);
      this.sources.push(src);
      started++;
    }

    this.startCtx = t0;
    this.startGlobal = fromGlobal;
    this.frozen = fromGlobal;
    this._playing = started > 0;
  }

  pause(): void {
    if (!this._playing) return;
    this.frozen = this.getTime();
    this.stopSources();
    this._playing = false;
    this.onlyStem = null;
  }

  seek(t: number, segments: ScheduleSegment[]): void {
    if (this._playing) {
      this.play(t, segments);
    } else {
      this.frozen = t;
    }
  }

  getTime(): number {
    if (!this._playing || !this.ctx) return this.frozen;
    return Math.max(this.frozen, this.startGlobal + (this.ctx.currentTime - this.startCtx));
  }

  setFrozen(t: number): void {
    this.frozen = t;
  }

  private stopSources(): void {
    for (const s of this.sources) {
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
      try {
        s.disconnect();
      } catch {
        /* already disconnected */
      }
    }
    this.sources = [];
  }
}
