"""Synthetic replay and strictly local Ollama transports for the pilot."""

from __future__ import annotations

import json
import hashlib
import http.client
import math
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class BackendError(Exception):
    """Backend failure with a stable, non-sensitive code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _reject_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite JSON constant")


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("nonfinite JSON number")
    return parsed


def _strict_json(data):
    return json.loads(data, object_pairs_hook=_reject_pairs,
                      parse_constant=_reject_constant, parse_float=_finite_float)


class ReplayBackend:
    name = "replay"
    model = "scripted-fixture"

    def __init__(self, path):
        try:
            raw = Path(path).read_bytes()
            data = _strict_json(raw)
        except (OSError, UnicodeError, ValueError) as exc:
            raise BackendError("replay_invalid") from exc
        if not isinstance(data, dict) or not all(
            isinstance(case, str) and isinstance(entries, list)
            and all(isinstance(entry, str) or entry == {"error": "fixture_failure"}
                    for entry in entries)
            for case, entries in data.items()
        ):
            raise BackendError("replay_invalid")
        self._data = data
        self._digest = hashlib.sha256(raw).hexdigest()
        self._case = None
        self._position = 0

    def metadata(self):
        return {"synthetic_replay": True, "source": "fixture", "fixture_sha256": self._digest}

    def start_case(self, case_id):
        self._case = case_id
        self._position = 0

    def generate(self, messages, *, seed, temperature, max_tokens, timeout):
        entries = self._data.get(self._case, [])
        if self._position >= len(entries):
            raise BackendError("replay_missing")
        entry = entries[self._position]
        self._position += 1
        if isinstance(entry, dict):
            raise BackendError("fixture_failure")
        return {"text": entry, "finish_reason": None, "usage": {}}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


_MAX_RESPONSE_BYTES = 4 * 1024 * 1024

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "minLength": 1},
        "explanation": {"type": "string", "minLength": 1},
        "optional_next_step": {"type": ["string", "null"]},
    },
    "required": ["answer", "explanation", "optional_next_step"],
    "additionalProperties": False,
}


class OllamaBackend:
    name = "ollama"

    def __init__(self, model, base_url="http://127.0.0.1:11434", *, thinking="default"):
        if thinking not in ("default", "on", "off"):
            raise BackendError("invalid_thinking_mode")
        if not isinstance(model, str) or not model.strip() or model != model.strip():
            raise BackendError("invalid_model")
        # Ollama cloud model identifiers contain the cloud suffix.
        if ":cloud" in model.lower() or model.lower().endswith("-cloud"):
            raise BackendError("cloud_model_forbidden")
        if not isinstance(base_url, str):
            raise BackendError("invalid_endpoint")
        try:
            parsed = urllib.parse.urlsplit(base_url)
            port = parsed.port
        except ValueError as exc:
            raise BackendError("invalid_endpoint") from exc
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "[::1]", "::1")
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment or port == 0
                or not parsed.netloc):
            raise BackendError("invalid_endpoint")
        # Reject hostname spelling tricks and any stray delimiters in netloc.
        host = parsed.hostname
        expected = ("[::1]" if host == "::1" else host) + (f":{port}" if port is not None else "")
        if parsed.netloc != expected:
            raise BackendError("invalid_endpoint")
        self.model = model
        self.base_url = base_url
        self.thinking = thinking
        self._metadata = None
        self._thinking_error = None
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def metadata(self):
        if self._metadata is not None:
            return dict(self._metadata)
        metadata = {
            "endpoint": self.base_url,
            "requested_model": self.model,
            "model_digest": None,
            "server_version": None,
            "resolved_model": None,
            "thinking_mode": "runtime/model default" if self.thinking == "default" else self.thinking,
            "requested_thinking_mode": self.thinking,
            "thinking_capabilities": None,
            "metadata_errors": {},
        }
        # Discovery is best effort for default mode and never performs inference.
        discovered = {}
        for endpoint, body in (("version", None), ("show", {"model": self.model}), ("tags", None)):
            try:
                discovered[endpoint] = self._request("/api/" + endpoint, body, timeout=2.0)
            except BackendError as exc:
                metadata["metadata_errors"][endpoint] = exc.code
        version = discovered.get("version", {}).get("version")
        if isinstance(version, str):
            metadata["server_version"] = version
        capability = discovered.get("show", {}).get("thinking")
        advertised = discovered.get("show", {}).get("capabilities")
        if capability is None and isinstance(advertised, list) and "thinking" in advertised:
            capability = {"values": [True, False], "source": "capabilities"}
        if isinstance(capability, dict):
            metadata["thinking_capabilities"] = capability
        models = discovered.get("tags", {}).get("models", [])
        # Bare model names resolve to :latest. Do not guess other aliases.
        names = {self.model}
        if ":" not in self.model.rsplit("/", 1)[-1]:
            names.add(self.model + ":latest")
        if isinstance(models, list):
            for model in models:
                if isinstance(model, dict) and isinstance(model.get("name"), str) and model["name"] in names:
                    metadata["resolved_model"] = model["name"]
                    digest = model.get("digest")
                    metadata["model_digest"] = digest if isinstance(digest, str) else None
                    break
        if self.thinking != "default":
            values = capability.get("values") if isinstance(capability, dict) else None
            if not isinstance(values, list) or not values or any(type(v) not in (bool, str) for v in values):
                self._thinking_error = "thinking_support_unverified"
            elif not any(v is (self.thinking == "on") for v in values):
                self._thinking_error = "thinking_mode_unsupported"
        metadata["thinking_control_error"] = self._thinking_error
        self._metadata = metadata
        return dict(metadata)

    def start_case(self, case_id):
        pass

    def preflight(self):
        if self.thinking != "default":
            self.metadata()
            if self._thinking_error:
                raise BackendError(self._thinking_error)

    def generate(self, messages, *, seed, temperature, max_tokens, timeout):
        self.preflight()
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": _RESPONSE_SCHEMA,
            "options": {"seed": seed, "temperature": temperature, "num_predict": max_tokens},
        }
        if self.thinking != "default":
            body["think"] = self.thinking == "on"
        payload = self._request("/api/chat", body, timeout=timeout)
        try:
            content = payload["message"]["content"]
            thinking = payload["message"].get("thinking")
            if (not isinstance(content, str)
                    or (thinking is not None and not isinstance(thinking, str))
                    or not isinstance(payload.get("model"), str)
                    or not payload["model"] or payload.get("done") is not True):
                raise ValueError("invalid envelope")
            reason = payload.get("done_reason")
            if reason is not None and not isinstance(reason, str):
                raise ValueError("invalid finish reason")
            usage = {}
            for key in ("prompt_eval_count", "eval_count"):
                count = payload.get(key)
                if count is not None:
                    if type(count) is not int or count < 0:
                        raise ValueError("invalid token count")
                    usage[key] = count
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise BackendError("invalid_envelope") from exc
        return {
            "text": content, "thinking": thinking, "finish_reason": reason,
            "usage": usage, "model": payload["model"],
            "diagnostics": {
                "requested_thinking_mode": self.thinking,
                "thinking_returned": bool(thinking),
                "thinking_characters": len(thinking) if thinking is not None else 0,
                "content_characters": len(content),
                # Ollama exposes a combined generation count, not a reliable split.
                "thinking_tokens": None, "content_tokens": None,
                "generated_tokens": usage.get("eval_count"),
            },
            "control_error": "thinking_mode_not_honored" if self.thinking == "off" and thinking else None,
        }

    def _request(self, path, body, *, timeout):
        request = urllib.request.Request(
            self.base_url + path,
            data=None if body is None else json.dumps(body, allow_nan=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST",
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None:
                    try:
                        expected_length = int(declared_length)
                    except ValueError as exc:
                        raise BackendError("invalid_envelope") from exc
                    if expected_length > len(raw) and len(raw) <= _MAX_RESPONSE_BYTES:
                        raise BackendError("connection_error")
        except BackendError:
            raise
        except urllib.error.HTTPError as exc:
            exc.close()
            raise BackendError("http_error") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise BackendError("timeout") from exc
        except (urllib.error.URLError, ConnectionError, OSError, http.client.HTTPException) as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise BackendError("timeout") from exc
            raise BackendError("connection_error") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise BackendError("invalid_envelope")
        try:
            payload = _strict_json(raw)
            if not isinstance(payload, dict):
                raise ValueError("invalid envelope")
        except (UnicodeError, ValueError, TypeError, KeyError) as exc:
            raise BackendError("invalid_envelope") from exc
        return payload
