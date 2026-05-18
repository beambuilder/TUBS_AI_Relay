"""TUBS AI Relay — OpenAI-compatible proxy for TU Braunschweig KI-Toolbox.

Exposes:
    GET  /                       Chat UI
    GET  /healthz                Health probe
    GET  /v1/models              OpenAI-compatible model list
    POST /v1/chat/completions    OpenAI-compatible chat completions (stream + non-stream)
    GET  /api/config             Lightweight config for the UI
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles


# -------------------------------------------------------------------------- #
# Constants — mirror python-tubskitb
# -------------------------------------------------------------------------- #

API_CLOUD_BASE_URL = "https://ki-toolbox.tu-braunschweig.de/api/v1/chat"
API_LOCAL_BASE_URL = "https://ki-toolbox.tu-braunschweig.de/api/v1/localChat"

CLOUD_MODELS: list[str] = [
    "gpt-5.1",
    "gpt-5",
    "gpt-4.1",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-4",
    "gpt-3.5-turbo-0125",
    "o4-mini",
    "o3",
    "o3-mini",
    "o1",
]

LOCAL_MODELS: list[str] = [
    "openai/gpt-oss-120b",
    "Qwen/Qwen3-30B-A3B",
    "Qwen/Qwen2.5-Coder-32B-Instruct",
    "mistralai/Mistral-Small-24B-Instruct-2501",
    "microsoft/phi-4",
]

ALL_MODELS: list[str] = CLOUD_MODELS + LOCAL_MODELS
CLOUD_SET = set(CLOUD_MODELS)
LOCAL_SET = set(LOCAL_MODELS)


# -------------------------------------------------------------------------- #
# Config
# -------------------------------------------------------------------------- #


def _read_secret_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def get_tubs_api_key() -> str:
    """Read TUBS API key from env, falling back to a mounted secret file."""
    env_key = os.getenv("TUBS_API_KEY", "").strip()
    if env_key:
        return env_key
    file_path = os.getenv("TUBS_API_KEY_FILE", "/data/tubs_api_key").strip()
    if file_path:
        return _read_secret_file(file_path)
    return ""


DEFAULT_MODEL = os.getenv("TUBS_DEFAULT_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
RELAY_API_KEY = os.getenv("RELAY_API_KEY", "").strip()
STATIC_DIR = Path(__file__).resolve().parent / "static"


# -------------------------------------------------------------------------- #
# Helpers
# -------------------------------------------------------------------------- #


def base_url_for(model: str) -> str:
    if model in LOCAL_SET:
        return API_LOCAL_BASE_URL
    return API_CLOUD_BASE_URL


def build_prompt(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Translate OpenAI messages -> single TUBS prompt + customInstructions."""
    system_parts: list[str] = []
    convo: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if content is None:
            continue
        # Tolerate OpenAI vision-style content arrays
        if isinstance(content, list):
            content = "".join(
                str(p.get("text", "")) for p in content if isinstance(p, dict)
            )
        elif not isinstance(content, str):
            content = str(content)
        if role == "system":
            system_parts.append(content)
        elif role in ("user", "assistant", "tool", "function"):
            convo.append({"role": role, "content": content})

    if not convo:
        raise HTTPException(
            status_code=400,
            detail="messages must contain at least one user or assistant entry",
        )

    system_prompt = "\n\n".join(p for p in system_parts if p.strip())

    if len(convo) == 1 and convo[0]["role"] == "user":
        return convo[0]["content"], system_prompt

    last = convo[-1]
    prior = convo[:-1]
    lines: list[str] = []
    for m in prior:
        role = m["role"]
        label = {
            "user": "User",
            "assistant": "Assistant",
            "tool": "Tool",
            "function": "Tool",
        }.get(role, role.capitalize())
        lines.append(f"{label}: {m['content']}")

    history = "\n\n".join(lines)
    last_label = "User" if last["role"] == "user" else last["role"].capitalize()
    prompt = (
        "You are continuing an ongoing conversation. The transcript so far is:\n\n"
        f"{history}\n\n"
        f"New {last_label} message:\n{last['content']}\n\n"
        f"Please reply directly to that new {last_label.lower()} message."
    )
    return prompt, system_prompt


def require_relay_auth(request: Request) -> None:
    if not RELAY_API_KEY:
        return
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    if token != RELAY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid token")


# -------------------------------------------------------------------------- #
# FastAPI app
# -------------------------------------------------------------------------- #

app = FastAPI(title="TUBS AI Relay", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "tubs_api_key_configured": bool(get_tubs_api_key()),
        "relay_auth_required": bool(RELAY_API_KEY),
        "default_model": DEFAULT_MODEL,
        "models": ALL_MODELS,
    }


@app.get("/api/config")
async def api_config(request: Request) -> dict[str, Any]:
    require_relay_auth(request)
    return {
        "default_model": DEFAULT_MODEL,
        "cloud_models": CLOUD_MODELS,
        "local_models": LOCAL_MODELS,
        "tubs_api_key_configured": bool(get_tubs_api_key()),
        "version": app.version,
    }


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
async def favicon() -> Response:
    favicon_path = STATIC_DIR / "favicon.svg"
    if favicon_path.exists():
        return FileResponse(favicon_path, media_type="image/svg+xml")
    return Response(status_code=204)


@app.get("/v1/models")
async def list_models(request: Request) -> dict[str, Any]:
    require_relay_auth(request)
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": now, "owned_by": "tubs"}
            for m in ALL_MODELS
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    require_relay_auth(request)
    api_key = get_tubs_api_key()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "TUBS_API_KEY is not configured. Set the environment variable "
                "or place the token at the path defined by TUBS_API_KEY_FILE."
            ),
        )

    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages array is required")

    model = body.get("model") or DEFAULT_MODEL
    if not isinstance(model, str) or not model:
        raise HTTPException(status_code=400, detail="model must be a non-empty string")

    stream = bool(body.get("stream", False))
    prompt, system_prompt = build_prompt(messages)

    url = f"{base_url_for(model)}/send"
    payload = {
        "thread": None,
        "prompt": prompt,
        "model": model,
        "customInstructions": system_prompt,
        "hideCustomInstructions": True,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    if stream:
        return StreamingResponse(
            _stream_tubs(url, headers, payload, completion_id, created, model),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    return await _complete_tubs(url, headers, payload, completion_id, created, model)


# -------------------------------------------------------------------------- #
# TUBS calls
# -------------------------------------------------------------------------- #

TIMEOUT = httpx.Timeout(connect=20.0, read=600.0, write=30.0, pool=30.0)


async def _stream_tubs(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    completion_id: str,
    created: int,
    model: str,
) -> AsyncIterator[bytes]:
    yield _sse_chunk(completion_id, created, model, role="assistant")
    finish_reason = "stop"
    usage: dict[str, int] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async with client.stream(
                "POST", url, headers=headers, json=payload
            ) as resp:
                if resp.status_code != 200:
                    detail = (await resp.aread()).decode("utf-8", errors="replace")
                    yield _sse_error(f"TUBS API {resp.status_code}: {detail}")
                    yield b"data: [DONE]\n\n"
                    return
                got_done = False
                async for line in resp.aiter_lines():
                    if not line or not line.strip():
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    etype = evt.get("type")
                    if etype == "chunk":
                        content = evt.get("content") or ""
                        if content:
                            yield _sse_chunk(
                                completion_id, created, model, content=content
                            )
                    elif etype == "done":
                        got_done = True
                        usage = {
                            "prompt_tokens": int(evt.get("promptTokens") or 0),
                            "completion_tokens": int(evt.get("responseTokens") or 0),
                            "total_tokens": int(evt.get("totalTokens") or 0),
                        }
                    elif etype == "error":
                        yield _sse_error(
                            evt.get("message") or evt.get("response") or "Upstream error"
                        )
                        finish_reason = "stop"
                if not got_done:
                    finish_reason = "stop"
    except httpx.TimeoutException:
        yield _sse_error("Upstream timeout talking to TUBS KI-Toolbox")
    except httpx.HTTPError as e:
        yield _sse_error(f"Upstream error: {e}")
    finally:
        yield _sse_final(completion_id, created, model, finish_reason, usage)
        yield b"data: [DONE]\n\n"


async def _complete_tubs(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    completion_id: str,
    created: int,
    model: str,
) -> JSONResponse:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Upstream timeout")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code if resp.status_code >= 400 else 502,
            detail=f"TUBS API error: {resp.text}",
        )

    content_parts: list[str] = []
    final_event: dict[str, Any] | None = None
    for line in resp.text.splitlines():
        if not line.strip():
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type")
        if etype == "chunk":
            piece = evt.get("content")
            if piece:
                content_parts.append(piece)
        elif etype == "done":
            final_event = evt
        elif etype == "error":
            raise HTTPException(
                status_code=502,
                detail=evt.get("message") or "Upstream error from TUBS",
            )

    text = "".join(content_parts)
    if not text and final_event:
        text = final_event.get("response", "") or ""

    usage = {
        "prompt_tokens": int((final_event or {}).get("promptTokens") or 0),
        "completion_tokens": int((final_event or {}).get("responseTokens") or 0),
        "total_tokens": int((final_event or {}).get("totalTokens") or 0),
    }

    return JSONResponse(
        {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": usage,
        }
    )


# -------------------------------------------------------------------------- #
# SSE serialization
# -------------------------------------------------------------------------- #


def _sse_chunk(
    cid: str,
    created: int,
    model: str,
    content: str | None = None,
    role: str | None = None,
) -> bytes:
    delta: dict[str, Any] = {}
    if role is not None:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    obj = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


def _sse_final(
    cid: str,
    created: int,
    model: str,
    finish_reason: str,
    usage: dict[str, int],
) -> bytes:
    obj = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        "usage": usage,
    }
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


def _sse_error(message: str) -> bytes:
    obj = {"error": {"message": message, "type": "upstream_error"}}
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")
