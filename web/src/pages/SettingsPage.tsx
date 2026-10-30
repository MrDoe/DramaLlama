import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, CheckCircle, WarningCircle } from "@phosphor-icons/react";
import { api } from "../api";
import type { LlmConfig, LlmTestResult } from "../api";

function sourceLabel(source: string | undefined): string {
  if (source === "file") return "saved in settings";
  if (source === "env") return "from environment";
  return "default";
}

export function SettingsPage() {
  const [config, setConfig] = useState<LlmConfig | null>(null);
  const [sunoPlan, setSunoPlan] = useState<"free" | "pro">("free");
  const [savingPlan, setSavingPlan] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [protocol, setProtocol] = useState<"ollama" | "openai">("ollama");
  const [url, setUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [removeKey, setRemoveKey] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [excludedCount, setExcludedCount] = useState(0);
  const [customModel, setCustomModel] = useState(false);
  const lastTested = useRef("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<LlmTestResult | null>(null);

  function markEdited(): void {
    setTest(null);
    setNotice("");
  }
  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .getConfig()
      .then((cfg) => {
        if (!active) return;
        setConfig(cfg.llm);
        if (cfg.suno) setSunoPlan(cfg.suno.plan);
        setProtocol(cfg.llm.api);
        setUrl(cfg.llm.url);
        setModel(cfg.llm.model);
      })
      .catch((reason: Error) => {
        if (active) setError(reason.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runTest(override?: {
    api: string;
    url: string;
    model: string;
  }): Promise<void> {
    const key = override
      ? `${override.api}|${override.url.trim()}`
      : `${protocol}|${url.trim()}`;
    lastTested.current = key;
    setTesting(true);
    try {
      const payload = override
        ? { ...override, api_key: apiKey ? apiKey : null }
        : undefined;
      const result = await api.testConfig(payload);
      setTest(result);
      // Older backends omit `excluded` (and may omit `models` on failure);
      // normalize so the dropdown still works against them.
      const usable = result.models ?? [];
      setExcludedCount(result.excluded?.length ?? 0);
      if (result.ok && usable.length > 0) setModels(usable);
    } catch {
      setTest(null);
    } finally {
      setTesting(false);
    }
  }

  useEffect(() => {
    if (loading || config === null || testing) return;
    const trimmed = url.trim();
    if (!trimmed || !/^https?:\/\//i.test(trimmed)) return;
    if (`${protocol}|${trimmed}` === lastTested.current) return;
    const timer = setTimeout(() => {
      void runTest({ api: protocol, url: trimmed, model: model.trim() });
    }, 600);
    return () => clearTimeout(timer);
  });

  const dirty =
    config !== null &&
    (protocol !== config.api ||
      url.trim() !== config.url ||
      model.trim() !== config.model ||
      apiKey !== "" ||
      removeKey);

  async function save(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const payload: {
        api: string;
        url: string;
        model: string;
        api_key?: string | null;
      } = { api: protocol, url: url.trim(), model: model.trim() };
      if (apiKey) payload.api_key = apiKey;
      else if (removeKey) payload.api_key = "";
      const cfg = await api.updateConfig(payload);
      setConfig(cfg.llm);
      setApiKey("");
      setRemoveKey(false);
      setNotice("Saved. The front-page engine stack already shows the new model.");
      void runTest({ api: protocol, url: url.trim(), model: model.trim() });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save settings.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page">
      <Link to="/" className="back-link">
        <ArrowLeft size={14} />
        Projects
      </Link>

      <div className="section-head">
        <div>
          <h2>Settings</h2>
          <p>Script-parsing LLM · saved to this machine, never the key itself</p>
        </div>
      </div>

      {error ? (
        <div className="error-banner" role="alert">
          <WarningCircle size={16} weight="fill" />
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="engine-note" role="status">
          <CheckCircle size={16} />
          {notice}
        </div>
      ) : null}

      {loading || config === null ? (
        <div className="project-grid">
          <div className="skeleton" />
        </div>
      ) : (
        <div className="settings-wrap">
          <form className="form-card" onSubmit={save}>
            <div className="field">
              <span className="label">
                Protocol <span>pick the endpoint family</span>
              </span>
              <div className="segmented" role="group" aria-label="LLM protocol">
                <button
                  type="button"
                  className={`segment${protocol === "ollama" ? " active" : ""}`}
                  onClick={() => {
                    setProtocol("ollama");
                    markEdited();
                  }}
                  aria-pressed={protocol === "ollama"}
                >
                  Ollama
                </button>
                <button
                  type="button"
                  className={`segment${protocol === "openai" ? " active" : ""}`}
                  onClick={() => {
                    setProtocol("openai");
                    markEdited();
                  }}
                  aria-pressed={protocol === "openai"}
                >
                  OpenAI-compatible
                </button>
              </div>
              <span className="panel-sub">
                {protocol === "ollama"
                  ? "Local Ollama or any Ollama-compatible endpoint."
                  : "Hosted endpoints such as OpenCode Go (chat/completions)."}
              </span>
            </div>

            <div className="field">
              <label className="label" htmlFor="llm-url">
                Endpoint URL <span>{sourceLabel(config.sources.url)}</span>
              </label>
              <input
                id="llm-url"
                className="input"
                value={url}
                onChange={(event) => {
                  setUrl(event.target.value);
                  markEdited();
                }}
                placeholder={
                  protocol === "openai"
                    ? "https://opencode.ai/inference/go/openai/v1"
                    : "http://127.0.0.1:11434"
                }
                inputMode="url"
                spellCheck={false}
              />
            </div>

            <div className="field">
              <label className="label" htmlFor="llm-model">
                Model <span>{sourceLabel(config.sources.model)}</span>
              </label>
              {models.length > 0 && !customModel ? (
                <select
                  id="llm-model"
                  className="input"
                  value={model}
                  onChange={(event) => {
                    if (event.target.value === "__custom") {
                      setCustomModel(true);
                    } else {
                      setModel(event.target.value);
                      markEdited();
                    }
                  }}
                >
                  {model === "" ? <option value="">Select a model…</option> : null}
                  {models.includes(model)
                    ? null
                    : model !== "" && (
                        <option value={model}>{model} (current)</option>
                      )}
                  {models.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                  <option value="__custom">Other (custom value)…</option>
                </select>
              ) : (
                <input
                  id="llm-model"
                  className="input"
                  value={model}
                  onChange={(event) => {
                    setModel(event.target.value);
                    setCustomModel(true);
                    markEdited();
                  }}
                  placeholder={protocol === "openai" ? "kimi-k3" : "gemma4:12b-16k"}
                  spellCheck={false}
                />
              )}
              {models.length > 0 ? (
                <span className="panel-sub">
                  {models.length} usable model{models.length === 1 ? "" : "s"}
                  {excludedCount > 0
                    ? ` · ${excludedCount} hidden (embeddings)`
                    : ""}
                  .{" "}
                  {customModel ? (
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => {
                        setCustomModel(false);
                        if (!models.includes(model)) setModel(models[0]);
                      }}
                    >
                      Back to the list
                    </button>
                  ) : (
                    "Editing the URL re-tests automatically."
                  )}
                </span>
              ) : (
                <span className="panel-sub">
                  Test the connection to load the endpoint&apos;s model list.
                </span>
              )}
            </div>

            <div className="field">
              <label className="label" htmlFor="llm-key">
                API key{" "}
                <span>
                  {config.apiKeyConfigured ? "saved · hidden" : "only needed for hosted endpoints"}
                </span>
              </label>
              <div className="key-row">
                <input
                  id="llm-key"
                  className="input"
                  type="password"
                  value={apiKey}
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    if (event.target.value) setRemoveKey(false);
                    markEdited();
                  }}
                  placeholder={
                    config.apiKeyConfigured
                      ? "•••••••• · leave blank to keep"
                      : "Bearer token for the endpoint"
                  }
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
              {config.apiKeyConfigured && !apiKey ? (
                removeKey ? (
                  <span className="panel-sub">
                    Saved key will be removed on save.{" "}
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => {
                        setRemoveKey(false);
                        markEdited();
                      }}
                    >
                      Undo
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => {
                      setRemoveKey(true);
                      markEdited();
                    }}
                  >
                    Remove saved key
                  </button>
                )
              ) : null}
            </div>

            <div className="status-line" aria-live="polite">
              {testing ? (
                <span className="panel-sub">Testing connection…</span>
              ) : test ? (
                test.ok ? (
                  <span className="status-line-ok">
                    <CheckCircle size={15} weight="fill" />
                    Connected · {test.provider} · {test.model}
                  </span>
                ) : (
                  <span className="status-line-bad">
                    <WarningCircle size={15} weight="fill" />
                    {test.error ?? "No response"}
                  </span>
                )
              ) : (
                <span className="panel-sub">Not tested yet.</span>
              )}
            </div>

            <div className="hero-actions">
              <button
                type="submit"
                className="button button-primary"
                disabled={saving || !dirty || !url.trim() || !model.trim()}
              >
                {saving ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                className="button button-secondary"
                disabled={testing || !url.trim()}
                onClick={() =>
                  void runTest({ api: protocol, url: url.trim(), model: model.trim() })
                }
              >
                {testing ? "Testing…" : "Test connection"}
              </button>
            </div>
          </form>

          <form
            className="form-card"
            onSubmit={(event) => {
              event.preventDefault();
              setSavingPlan(true);
              api
                .updateConfig(null, { plan: sunoPlan })
                .then((cfg) => {
                  if (cfg.suno) setSunoPlan(cfg.suno.plan);
                  setNotice("Suno plan saved.");
                })
                .catch((reason: Error) => setError(reason.message))
                .finally(() => setSavingPlan(false));
            }}
          >
            <div className="field">
              <span className="label">
                Suno plan <span>controls which generation options are sent</span>
              </span>
              <div className="segmented" role="group" aria-label="Suno plan">
                <button
                  type="button"
                  className={`segment${sunoPlan === "free" ? " active" : ""}`}
                  onClick={() => setSunoPlan("free")}
                  aria-pressed={sunoPlan === "free"}
                >
                  Free
                </button>
                <button
                  type="button"
                  className={`segment${sunoPlan === "pro" ? " active" : ""}`}
                  onClick={() => setSunoPlan("pro")}
                  aria-pressed={sunoPlan === "pro"}
                >
                  Pro / Premium
                </button>
              </div>
              <span className="panel-sub">
                {sunoPlan === "free"
                  ? "Style influence, weirdness, audio influence and voice personas are withheld — Suno rejects them on free accounts."
                  : "All generation knobs and voice personas are sent."}
              </span>
            </div>
            <div className="hero-actions">
              <button type="submit" className="button button-primary" disabled={savingPlan}>
                {savingPlan ? "Saving…" : "Save plan"}
              </button>
            </div>
          </form>

          <div className="engine-note">
            <WarningCircle size={16} />
            Settings file beats environment per key; unset keys still follow
            DRAMA_LLAMA_LLM_URL / DRAMA_LLAMA_LLM / DRAMA_LLAMA_LLM_API_KEY,
            and the plan follows DRAMA_LLAMA_SUNO_PLAN.
          </div>
        </div>
      )}
    </div>
  );
}
