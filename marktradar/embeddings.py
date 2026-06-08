"""Ollama-Backends für Embeddings (bge-m3, 1024d) + Chat (qwen).

Embedding- und Chat-Backend sind ENTKOPPELT, damit der geteilte GPU-Host nicht
ständig bge-m3 ↔ qwen tauschen muss:
  - EMBED_HOST       (Query-Pfad, klein, kann z. B. Ollama Cloud sein)
  - OLLAMA_CHAT_HOST (Batch-qwen für ingest/classify/synthesize, z. B. GPU-Host)
Beide optional mit Bearer-Key (Ollama Cloud / geschützter Proxy).
"""
import os

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_HOST = os.getenv("EMBED_HOST", OLLAMA_HOST)
CHAT_HOST = os.getenv("OLLAMA_CHAT_HOST", OLLAMA_HOST)
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))
_EMBED_KEY = os.getenv("EMBED_API_KEY") or os.getenv("OLLAMA_API_KEY")
_CHAT_KEY = os.getenv("CHAT_API_KEY") or os.getenv("OLLAMA_API_KEY")


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
