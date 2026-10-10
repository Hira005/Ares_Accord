"""Tiny LLM client using only the standard library.

The provider is chosen from environment variables (see .env.example):
  GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY (+ optional OPENAI_BASE_URL for Groq, OpenRouter, local servers)
  LLM_PROVIDER = gemini | anthropic | openai   (optional; otherwise the first key found is used)

Every call asks for ONE JSON object. Anything that goes wrong raises LLMError and the caller
falls back to the clearly-labelled rule-based behaviour.
"""
import json
import os
import re
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


def provider():
    """Return (name, model) for the configured provider, or None."""
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    order = [explicit] if explicit else ["gemini", "anthropic", "openai"]
    for name in order:
        if name == "gemini" and os.getenv("GEMINI_API_KEY"):
            return "gemini", os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        if name == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic", os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
        if name in ("openai", "groq", "openrouter") and os.getenv("OPENAI_API_KEY"):
            return "openai", os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return None


def describe():
    p = provider()
    return f"{p[0]}:{p[1]}" if p else "none (rule-based fallback)"


def _post(url, headers, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "ares-accord/1.0 (python-urllib)", "Accept": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:200]
        raise LLMError(f"HTTP {e.code}: {detail}") from e
    except Exception as e:  # timeouts, DNS, TLS, bad JSON
        raise LLMError(f"{type(e).__name__}: {e}") from e


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("model did not return a JSON object")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"invalid JSON from model: {e}") from e
    if not isinstance(data, dict):
        raise LLMError("model JSON was not an object")
    return data


def call_json(system, prompt, timeout=25):
    """Return (data_dict, 'provider:model'). Raises LLMError on any failure."""
    p = provider()
    if not p:
        raise LLMError("no API key configured")
    name, model = p
    if name == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        out = _post(url, {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.5, "responseMimeType": "application/json"},
        }, timeout)
        try:
            text = out["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected Gemini response: {str(out)[:150]}") from e
    elif name == "anthropic":
        out = _post("https://api.anthropic.com/v1/messages",
                    {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                    {"model": model, "max_tokens": 600, "temperature": 0.5, "system": system,
                     "messages": [{"role": "user", "content": prompt}]}, timeout)
        try:
            text = out["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected Anthropic response: {str(out)[:150]}") from e
    else:  # OpenAI or any OpenAI-compatible endpoint
        base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        out = _post(f"{base}/chat/completions", {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                    {"model": model, "temperature": 0.5, "response_format": {"type": "json_object"},
                     "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
                    timeout)
        try:
            text = out["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected OpenAI response: {str(out)[:150]}") from e
    return _extract_json(text), f"{name}:{model}"
