"""Backend catalogue: what the running backends can actually serve.

MODEL_SPECS is Loco's recipe book - every model Loco knows how to route to,
with measured facts. The backends hold the real files. Before this module
existed the two were conflated: /v1/models published the recipe book, so a
client was told about eight chat models that were not on disk and could never
be loaded.

This module asks the backends instead:

* the llama.cpp endpoint /v1/models - in router mode it lists every file in
  --models-dir with a status (unloaded/loaded), in single-model mode it lists
  the one model it serves;
* the ollama endpoint /api/tags - the models it has pulled;
* the embedding sidecar - whether it is up at all.

Availability is therefore measured, not declared.
"""
import json
import time
import urllib.request

from . import config as C
from .models import AUTO_MODEL, EMBEDDING_MODELS, MODEL_SPECS, chat_model_payload

_TTL_SECONDS = 5.0
_cache: dict = {"at": 0.0, "llama": {}, "ollama": {}, "embedding": False}


def _get_json(url: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except Exception:
        return None


def _llama_models() -> dict:
    url = C.endpoints()["llama-cpp"].rstrip("/") + "/models"
    data = _get_json(url) or {}
    out = {}
    for entry in data.get("data") or []:
        model_id = str(entry.get("id") or "")
        if model_id:
            out[model_id] = entry
    return out


def _ollama_models() -> dict:
    url = C.endpoints()["ollama"].rstrip("/").removesuffix("/v1") + "/api/tags"
    data = _get_json(url) or {}
    out = {}
    for entry in data.get("models") or []:
        name = str(entry.get("name") or entry.get("model") or "")
        if name:
            out[name] = entry
    return out


def _embedding_sidecar_up() -> bool:
    from . import semantic
    url = semantic.EMBED_URL.removesuffix("/embeddings") + "/models"
    return _get_json(url) is not None


def _refresh() -> dict:
    now = time.monotonic()
    if now - _cache["at"] < _TTL_SECONDS:
        return _cache
    _cache["llama"] = _llama_models()
    _cache["ollama"] = _ollama_models()
    _cache["embedding"] = _embedding_sidecar_up()
    _cache["at"] = now
    return _cache


def reset_cache() -> None:
    """Forget the cached catalogue (tests, and after a backend change)."""
    _cache["at"] = 0.0


def _catalogue_id(spec, catalogue: dict):
    """Return the catalogue id that serves *spec*, or None.

    A backend may expose the model under its own id, an alias, or a
    differently cased name; all three count as available.
    """
    names = [spec.id, *spec.aliases]
    lowered = {name.lower(): name for name in catalogue}
    for name in names:
        if name in catalogue:
            return name
        hit = lowered.get(name.lower())
        if hit:
            return hit
    return None


def availability() -> dict:
    """model id -> {"available": bool, "backend": str, "catalogue_id": str|None}."""
    state = _refresh()
    llama, ollama = state["llama"], state["ollama"]
    out = {}
    for spec in MODEL_SPECS.values():
        if spec.id in EMBEDDING_MODELS:
            continue
        catalogue = llama if spec.backend == "llama-cpp" else ollama
        catalogue_id = _catalogue_id(spec, catalogue)
        out[spec.id] = {
            "available": catalogue_id is not None,
            "backend": spec.backend,
            "catalogue_id": catalogue_id,
        }
    return out


def is_available(model_id: str) -> bool:
    """Whether the backend can serve *model_id* right now."""
    spec = MODEL_SPECS.get(model_id)
    if spec is None:
        return False
    if spec.id in EMBEDDING_MODELS:
        return bool(_refresh()["embedding"])
    return bool(availability().get(spec.id, {}).get("available"))


def available_chat_ids() -> list:
    """Chat model ids the backends can serve, in registry order."""
    state = availability()
    return [model_id for model_id in MODEL_SPECS
            if model_id not in EMBEDDING_MODELS
            and state.get(model_id, {}).get("available")]


def models_payload(show_all: bool = False) -> dict:
    """The OpenAI-compatible model list, built from measured availability.

    By default only models the backends can really serve are published, so a
    client cannot register a model that will always fail. With show_all=True
    the whole registry is published and every entry carries an explicit
    "available" flag.
    """
    state = availability()
    embed_up = bool(_refresh()["embedding"])
    data = [{
        "id": AUTO_MODEL, "object": "model", "owned_by": "loco",
        "permission": [], "aliases": ["auto"], "available": True,
    }]
    for model in chat_model_payload():
        entry = dict(model)
        entry["available"] = bool(state.get(model["id"], {}).get("available"))
        if show_all or entry["available"]:
            data.append(entry)
    for model_id in sorted(EMBEDDING_MODELS):
        entry = {
            "id": model_id, "object": "model", "owned_by": "loco-recipe",
            "permission": [], "embedding": True, "available": embed_up,
        }
        if show_all or embed_up:
            data.append(entry)
    return {"object": "list", "data": data}
