"""Ollama-Embeddings (bge-m3, 1024d) — lokal, localhost:11434 (Dev-GPU)."""
import os

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))


def embed(text: str) -> list[float]:
    r = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    vec = r.json().get("embedding")
    if not vec or len(vec) != EMBED_DIM:
        raise ValueError(f"bge-m3 lieferte dim={len(vec) if vec else 0}, erwartet {EMBED_DIM}")
    return vec


def embed_batch(texts: list[str]) -> list[list[float]]:
    return [embed(t) for t in texts]
