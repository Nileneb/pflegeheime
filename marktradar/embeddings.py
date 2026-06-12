"""Ollama-Backends für Embeddings (bge-m3, 1024d) + Chat (qwen).

Embedding- und Chat-Backend sind ENTKOPPELT, damit der geteilte GPU-Host nicht
ständig bge-m3 ↔ qwen tauschen muss:
  - EMBED_HOST       (Query-Pfad, klein, kann z. B. Ollama Cloud sein)
  - OLLAMA_CHAT_HOST (Batch-qwen für ingest/classify/synthesize, z. B. GPU-Host)
Beide optional mit Bearer-Key (Ollama Cloud / geschützter Proxy).

chat_raw()/chat_json() sind der EINZIGE Chat-Callsite des Projekts — Modell,
Payload-Konvention (format=json, stream/think aus) und Host stehen nur hier,
alle Module konsumieren statt zu re-definieren.
"""
import json
import os

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_HOST = os.getenv("EMBED_HOST", OLLAMA_HOST)
CHAT_HOST = os.getenv("OLLAMA_CHAT_HOST", OLLAMA_HOST)
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen3.5:9b")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))
_EMBED_KEY = os.getenv("EMBED_API_KEY") or os.getenv("OLLAMA_API_KEY")
_CHAT_KEY = (os.getenv("CHAT_API_KEY") or os.getenv("OLLAMA_CLOUD_API_KEY")
             or os.getenv("OLLAMA_API_KEY"))


def _hdr(key):
    return {"Authorization": f"Bearer {key}"} if key else {}


def embed_headers():
    return _hdr(_EMBED_KEY)


def chat_headers():
    return _hdr(_CHAT_KEY)


def embed(text: str) -> list[float]:
    r = requests.post(
        f"{EMBED_HOST}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        headers=embed_headers(),
        timeout=60,
    )
    r.raise_for_status()
    vec = r.json().get("embedding")
    if not vec or len(vec) != EMBED_DIM:
        raise ValueError(f"bge-m3 lieferte dim={len(vec) if vec else 0}, erwartet {EMBED_DIM}")
    return vec


def embed_batch(texts: list[str]) -> list[list[float]]:
    return [embed(t) for t in texts]


def chat_raw(system: str, user: str, *, temperature: float = 0.0,
             num_ctx: int = 2048, num_predict: int = 200,
             timeout: int = 90) -> dict:
    """Ein JSON-Chat-Call gegen CHAT_HOST/CHAT_MODEL. Gibt die rohe `message`
    zurück ({content, thinking?}) — für Aufrufer, die den thinking-Channel
    auswerten müssen (Reasoning-Modelle). Fehler propagieren (raise_for_status)."""
    r = requests.post(
        f"{CHAT_HOST}/api/chat",
        json={"model": CHAT_MODEL, "format": "json", "stream": False, "think": False,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}],
              "options": {"temperature": temperature, "num_ctx": num_ctx,
                          "num_predict": num_predict}},
        headers=chat_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json().get("message", {}) or {}


def chat_json(system: str, user: str, *, temperature: float = 0.0,
              num_ctx: int = 2048, num_predict: int = 200,
              timeout: int = 90, empty=None):
    """chat_raw + content als JSON geparst. Leerer content → `empty`
    (statt JSONDecodeError), damit Aufrufer ihren Default-Typ bestimmen."""
    content = chat_raw(system, user, temperature=temperature, num_ctx=num_ctx,
                       num_predict=num_predict, timeout=timeout).get("content", "")
    if not (content or "").strip():
        return empty
    return json.loads(content)
