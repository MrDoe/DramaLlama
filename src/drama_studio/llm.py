from __future__ import annotations

import json
import re
import uuid
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from . import __version__
from .config import LLM_KEEP_ALIVE
from .models import ParseResult, ParseWarning
from .parser import parse_script
from .settings import LlmSettings, resolve_llm


class LLMError(RuntimeError):
    pass


class LLMParseError(LLMError):
    pass


NORMALIZE_SYSTEM_PROMPT = """\
You are a script formatter for an audio-drama production tool.
Rewrite the script below in the canonical screenplay format. Output ONLY the
formatted script — no JSON, no explanations, no code fences.

Canonical format:
# Title of the drama

## SCENE 1 — heading as written
SPEAKER (direction): spoken text here
[SFX: sound or ambience description]
SPEAKER: more spoken text

Rules:
- One speech = one line: `NAME (direction): text`. Names are ALL CAPS.
- Convert every attribution style to that form: `[NAME] "text"`,
  `Name: "text"`, `NAME (direction) text` without a colon, and `**NAME**
  (direction)` headers whose dialogue follows on later lines (join it into
  the single line).
- Directions in parentheses after the name go into `(direction)`. A lone
  `(note)` line after a speaker header merges into that line's direction.
  No direction → `NAME: text` with no parentheses.
- Strip wrapping quotes around dialogue (`"Ja mei."` becomes `Ja mei.`);
  keep quotes inside the text.
- Copy every spoken line verbatim — dialect, spelling and punctuation
  included. Never paraphrase, merge, split, reorder or invent dialogue. Keep
  inline vocal tags like <sigh> or <laugh> in the text.
- Stage directions and sound notes become `[text]` lines of their own.
- Scene headings become `## ...` lines with their original wording and order.
  If the script has no headings, start with `## SCENE 1`. The title becomes
  the `# Title` first line.
- Every spoken line appears exactly once.
"""


def normalize_messages(raw: str, language: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": NORMALIZE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Language: {'German' if language == 'de' else 'English'}\n"
                "Rewrite this script in the canonical format:\n\n"
                f"{raw}"
            ),
        },
    ]


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[\w+-]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def _normalize(text: str) -> str:
    text = re.sub(r"\*\*|__|\*|_", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.lower().split())


def _is_embedding_name(name: str) -> bool:
    return "embed" in name.lower()


class OllamaClient:
    """Ollama-compatible chat client (local Ollama or a hosted endpoint).

    Any endpoint speaking the Ollama `/api/chat` API works, including cloud
    offerings — pass an `api_key` (or set `DRAMA_LLAMA_LLM_API_KEY`) and it is
    sent as a Bearer token.
    """

    LOCAL_HOSTS: ClassVar[set[str]] = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

    api_name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 600.0,
        api_key: str | None = None,
    ) -> None:
        settings, _ = resolve_llm()
        self.base_url = (base_url or settings.url).rstrip("/")
        self.model = model or settings.model
        self.timeout_s = timeout_s
        self.api_key = api_key if api_key is not None else settings.api_key

    @property
    def provider(self) -> str:
        """`"local"` for loopback endpoints, `"cloud"` otherwise."""
        host = (urlparse(self.base_url).hostname or "").lower()
        return "local" if host in self.LOCAL_HOSTS else "cloud"

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        keep_alive: str | int = LLM_KEEP_ALIVE,
        model: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": temperature},
            "keep_alive": keep_alive,
        }
        if schema is not None:
            payload["format"] = schema

        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        content = (body.get("message") or {}).get("content", "")
        if not content:
            raise LLMError("Ollama returned an empty message")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ollama returned invalid JSON: {exc}") from exc

    def chat_text(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        keep_alive: str | int = LLM_KEEP_ALIVE,
        model: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": temperature},
            "keep_alive": keep_alive,
        }
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            content = (response.json().get("message") or {}).get("content", "")
        except Exception as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc
        if not content or not content.strip():
            raise LLMError("Ollama returned an empty message")
        return content

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        keep_alive: str | int = LLM_KEEP_ALIVE,
        model: str | None = None,
    ):
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {"temperature": temperature},
            "keep_alive": keep_alive,
        }
        if schema is not None:
            payload["format"] = schema
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout_s,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        body = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = (body.get("message") or {}).get("content", "")
                    if content:
                        yield content
                    if body.get("done"):
                        break
        except Exception as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

    def unload(self, model: str | None = None) -> None:
        try:
            httpx.post(
                f"{self.base_url}/api/generate",
                json={"model": model or self.model, "keep_alive": 0},
                headers=self._headers(),
                timeout=30,
            )
        except Exception:
            pass

    def available(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=3)
            response.raise_for_status()
            return True
        except Exception:
            return False

    def _model_capabilities(self, name: str) -> list[str] | None:
        """Capability tags via /api/show; None when the server can't tell."""
        try:
            response = httpx.post(
                f"{self.base_url}/api/show",
                json={"model": name},
                headers=self._headers(),
                timeout=5,
            )
            response.raise_for_status()
            capabilities = response.json().get("capabilities") or None
            return list(capabilities) if capabilities else None
        except Exception:
            return None

    def probe_models(self) -> tuple[list[str], list[str]]:
        """(usable, excluded) model names for the GUI picker.

        A model counts as usable when /api/show reports a completion
        capability, when the server reports no capabilities (older Ollama),
        or when probing fails and the name doesn't look like an embedding
        model. Embedding-only models land in excluded for transparency.
        """
        try:
            response = httpx.get(
                f"{self.base_url}/api/tags",
                headers=self._headers(),
                timeout=5,
            )
            response.raise_for_status()
            models = response.json().get("models") or []
            names = [m["name"] for m in models if m.get("name")]
        except Exception:
            return [], []
        usable: list[str] = []
        excluded: list[str] = []
        for name in names:
            capabilities = self._model_capabilities(name)
            if capabilities is None:
                (excluded if _is_embedding_name(name) else usable).append(name)
            elif "completion" in capabilities:
                usable.append(name)
            else:
                excluded.append(name)
        return usable, excluded

    def list_models(self) -> list[str]:
        """Best-effort chat-capable model names; [] when unreachable."""
        usable, _ = self.probe_models()
        return usable


class OpenAIChatClient:
    """OpenAI Chat Completions compatible client (e.g. OpenCode Go).

    Same duck type as :class:`OllamaClient` (`chat_json`, `chat_stream`,
    `unload`, `available`, `provider`), so it plugs into LLM parse, the parse
    stream endpoint and Suno lyric sheets unchanged. Ollama-only options
    (`keep_alive`) are accepted and ignored. Structured output requests
    `response_format: {"type": "json_object"}`; the verbatim validator plus
    the deterministic fallback remain the real guarantees.
    """

    api_name = "openai"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 600.0,
        api_key: str | None = None,
    ) -> None:
        settings, _ = resolve_llm()
        self.base_url = (base_url or settings.url).rstrip("/")
        self.model = model or settings.model
        self.timeout_s = timeout_s
        self.api_key = api_key if api_key is not None else settings.api_key

    @property
    def provider(self) -> str:
        """`"local"` for loopback endpoints, `"cloud"` otherwise."""
        host = (urlparse(self.base_url).hostname or "").lower()
        return "local" if host in OllamaClient.LOCAL_HOSTS else "cloud"

    def _headers(self, session_id: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"drama-llama/{__version__}",
            "x-opencode-session": session_id,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        keep_alive: str | int = LLM_KEEP_ALIVE,
        model: str | None = None,
    ) -> dict[str, Any]:
        del keep_alive  # Ollama-only option
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(uuid.uuid4().hex),
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise LLMError(f"OpenAI-compatible request failed: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"OpenAI-compatible reply had no content: {exc}"
            ) from exc
        if not content:
            raise LLMError("OpenAI-compatible reply was empty")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"OpenAI-compatible reply was not JSON: {exc}"
            ) from exc

    def chat_text(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        keep_alive: str | int = LLM_KEEP_ALIVE,
        model: str | None = None,
    ) -> str:
        del keep_alive  # Ollama-only option
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(uuid.uuid4().hex),
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise LLMError(f"OpenAI-compatible request failed: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"OpenAI-compatible reply had no content: {exc}"
            ) from exc
        if not content or not content.strip():
            raise LLMError("OpenAI-compatible reply was empty")
        return content

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        keep_alive: str | int = LLM_KEEP_ALIVE,
        model: str | None = None,
    ):
        del keep_alive  # Ollama-only option
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(uuid.uuid4().hex),
                timeout=self.timeout_s,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    text = line.strip()
                    if text.startswith("data:"):
                        text = text[5:].strip()
                    if text == "[DONE]":
                        break
                    if not text:
                        continue
                    try:
                        body = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    choices = body.get("choices") or []
                    delta = (choices[0].get("delta") if choices else {}) or {}
                    content = delta.get("content") or ""
                    if content:
                        yield content
        except Exception as exc:
            raise LLMError(f"OpenAI-compatible request failed: {exc}") from exc

    def unload(self, model: str | None = None) -> None:
        del model
        # nothing resident to unload; kept for duck-type compatibility

    def available(self) -> bool:
        _, status = self._fetch_models()
        return status is not None and status != 404

    def _catalog_urls(self) -> list[str]:
        base = self.base_url.rstrip("/")
        urls = [f"{base}/models"]
        if base.endswith("/openai/v1"):
            # OpenCode Go serves the catalog beside the API-family path.
            urls.append(f"{base[: -len('/openai/v1')]}/v1/models")
        return urls

    def _fetch_models(self) -> tuple[dict | None, int | None]:
        """First non-404 catalog reply. (None, None) means unreachable."""
        for url in self._catalog_urls():
            try:
                response = httpx.get(
                    url,
                    headers=self._headers(uuid.uuid4().hex),
                    timeout=5,
                )
            except Exception:
                return None, None
            if response.status_code == 404:
                continue
            try:
                return response.json(), response.status_code
            except ValueError:
                return None, response.status_code
        return None, 404

    def probe_models(self) -> tuple[list[str], list[str]]:
        """(usable, excluded) model ids. The catalog carries no capability
        info, so only obvious embedding ids are excluded."""
        body, status = self._fetch_models()
        if body is None or status != 200:
            return [], []
        data = body.get("data") or []
        ids = [m["id"] for m in data if isinstance(m, dict) and m.get("id")]
        return (
            [i for i in ids if not _is_embedding_name(i)],
            [i for i in ids if _is_embedding_name(i)],
        )

    def list_models(self) -> list[str]:
        """Best-effort model ids for the GUI picker; [] when unreachable."""
        usable, _ = self.probe_models()
        return usable

    def check(self) -> dict[str, Any]:
        """Reachability + auth + catalog in one pass for the test endpoint."""
        body, status = self._fetch_models()
        if status is None:
            return {
                "ok": False,
                "models": [],
                "excluded": [],
                "error": f"no response at {self.base_url}",
            }
        if status in (401, 403):
            hint = (
                "free-tier models refuse other clients; use a Go subscription"
                if status == 403
                else "check the API key"
            )
            return {
                "ok": False,
                "models": [],
                "excluded": [],
                "error": f"endpoint rejected the request (HTTP {status}): {hint}",
            }
        if status == 404 or body is None:
            return {
                "ok": False,
                "models": [],
                "excluded": [],
                "error": f"no model catalog at {self.base_url} (HTTP 404)",
            }
        if status != 200:
            return {
                "ok": False,
                "models": [],
                "excluded": [],
                "error": f"endpoint error (HTTP {status})",
            }
        usable, excluded = self.probe_models()
        return {"ok": True, "models": usable, "excluded": excluded, "error": None}


LLMClient = OllamaClient | OpenAIChatClient


def make_llm_client_for(settings: LlmSettings) -> LLMClient:
    """Build a client for explicit settings (used by the config test endpoint)."""
    kwargs = {
        "base_url": settings.url,
        "model": settings.model,
        "api_key": settings.api_key,
    }
    if settings.api == "openai":
        return OpenAIChatClient(**kwargs)
    return OllamaClient(**kwargs)


def make_llm_client() -> LLMClient:
    """Build the configured LLM client (Ollama or OpenAI-compatible)."""
    settings, _ = resolve_llm()
    return make_llm_client_for(settings)


def parse_normalized(canonical: str, raw: str, language: str) -> ParseResult:
    """Deterministic parse of normalized text plus the verbatim gate.

    Shared by the blocking parse and the streaming endpoint so both enforce
    the same bar: every parsed line must survive verbatim in the pasted
    source. The LLM's job is only to reformat; structure comes from the
    deterministic parser.
    """
    result = parse_script(canonical, language)
    total = sum(len(scene.lines) for scene in result.script.scenes)
    if total == 0:
        raise LLMParseError("LLM normalization produced no dialogue lines")
    raw_norm = _normalize(raw)
    matched = 0
    for scene in result.script.scenes:
        for line in scene.lines:
            if _normalize(line.text) and _normalize(line.text) in raw_norm:
                matched += 1
            else:
                result.report.warnings.append(
                    ParseWarning(
                        source_line=0,
                        message="LLM line is not a verbatim substring of the source",
                        text=line.text[:120],
                    )
                )
    coverage = matched / total
    if coverage < 0.85:
        raise LLMParseError(
            f"LLM dialogue coverage is only {coverage:.0%}; "
            "refusing to replace the source text"
        )
    result.report.method = "llm"
    return result


def normalize_llm_text(
    raw: str,
    language: str = "en",
    *,
    client: LLMClient | None = None,
    model: str | None = None,
) -> str:
    """Rewrite an arbitrary script into the canonical parser format."""
    client = client or make_llm_client()
    text = client.chat_text(
        normalize_messages(raw, language),
        temperature=0.2,
        keep_alive=0,
        model=model,
    )
    return _strip_fences(text)


def llm_parse_script(
    raw: str,
    language: str = "en",
    *,
    client: LLMClient | None = None,
    model: str | None = None,
) -> ParseResult:
    """LLM parse: normalize any input format, then parse deterministically."""
    canonical = normalize_llm_text(raw, language, client=client, model=model)
    return parse_normalized(canonical, raw, language)
