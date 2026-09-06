"""One strict HTTP declaration parser shared by validation, execution and replay.

Absolute URLs retain ordinary HTTP port semantics (80 if omitted). Relative
origin-form targets address the service port allocated by the executor. No DNS,
TLS, userinfo, fragments, forward proxies or protocol switching are supported.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from ..chain import canonical_json
from .redaction import redact_value

_AUTHORITY = re.compile(r"(?:127\.0\.0\.1|\[::1\])(?::([1-9][0-9]{0,4}))?\Z")
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_PATH_SAFE = "/?=&%:@+,;$!*'()~[]"
_RESERVED_HEADERS = {"host", "connection", "upgrade", "content-length", "transfer-encoding", "trailer", "te"}


def _port(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("HTTP port must be an integer from 1 to 65535")
    return value


def request_identity(request: Any, service_port: int | None = None) -> dict[str, Any]:
    """Reject ambiguous declarations before transport; describe exactly its target."""
    if not isinstance(request, dict) or set(request) - {"url", "path", "method", "headers", "body"}:
        raise ValueError("HTTP request must be an object with supported fields")
    method = request.get("method", "GET")
    if not isinstance(method, str) or not _TOKEN.fullmatch(method) or method != method.upper() or method == "CONNECT":
        raise ValueError("HTTP method must be an uppercase token; CONNECT is unsupported")
    headers = request.get("headers", {})
    if not isinstance(headers, dict):
        raise ValueError("HTTP headers must be an object")
    names = set()
    for name, value in headers.items():
        if not isinstance(name, str) or not _TOKEN.fullmatch(name) or not isinstance(value, str):
            raise ValueError("HTTP header names and values must be valid strings")
        lowered = name.lower()
        if lowered in names or lowered in _RESERVED_HEADERS or lowered.startswith("proxy-"):
            raise ValueError("HTTP routing, framing and duplicate headers are unsupported")
        if any(ord(char) < 32 or ord(char) == 127 or ord(char) > 255 for char in value):
            raise ValueError("HTTP header value contains unsupported characters")
        names.add(lowered)
    host = "127.0.0.1"
    port = _port(service_port) if service_port is not None else None
    declared_url = request.get("url")
    if "url" in request:
        if "path" in request:
            raise ValueError("HTTP request must declare url or path, never both")
        if not isinstance(declared_url, str) or not declared_url.startswith("http://"):
            raise ValueError("only explicit lowercase http:// URLs are supported")
        if any(ord(char) <= 32 or ord(char) == 127 for char in declared_url) or "\\" in declared_url or "#" in declared_url:
            raise ValueError("HTTP URL contains ambiguous whitespace, backslash or fragment")
        parsed = urlsplit(declared_url)
        authority = _AUTHORITY.fullmatch(parsed.netloc)
        if not authority:
            raise ValueError("external service outside the verification boundary: URL requires literal 127.0.0.1 or [::1] and a valid port, without userinfo")
        host = parsed.hostname
        port = _port(int(authority.group(1))) if authority.group(1) else 80
        if service_port is not None and port != service_port:
            raise ValueError("external service outside the verification boundary: URL port is not the declared service's port")
        path = parsed.path or "/"
        # An empty query delimiter is still part of the declared request target.
        if "?" in declared_url:
            path += "?" + parsed.query
    else:
        path = request.get("path", "/")
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise ValueError("HTTP path must be an origin-form request target")
    if "#" in path or "\\" in path or any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise ValueError("HTTP path contains a fragment, backslash or control character")
    if re.search(r"%(?![0-9A-Fa-f]{2})", path):
        raise ValueError("HTTP path contains a malformed percent escape")
    target = quote(path, safe=_PATH_SAFE)
    authority_text = "[::1]" if host == "::1" else host
    return {
        "scheme": "http", "host": host, "port": port, "method": method,
        "target": target, "url": f"http://{authority_text}:{port}{target}" if port is not None else None,
        "declared_url": declared_url,
        "declaration_sha256": hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest(),
    }


def validate_requests(requests: Any, service_port: int | None = None) -> list[dict[str, Any]]:
    if not isinstance(requests, list):
        raise ValueError("HTTP requests must be a list")
    return [request_identity(request, service_port) for request in requests]


def response_problem(record: Any, requests: Any = None) -> str | None:
    """A transport diagnostic is evidence of an attempt, never an HTTP response.

    An HTTP probe declares the whole request batch. Partial batches do not
    supply that observable, even when their successful prefix is identical.
    """
    if not isinstance(record, dict):
        return "HTTP responses were not obtained"
    responses = record.get("responses")
    if not isinstance(responses, list) or not responses:
        return "HTTP responses were not obtained"
    if isinstance(requests, list) and len(responses) != len(requests):
        return "HTTP response count differs from declared requests"
    for index, response in enumerate(responses, 1):
        if not isinstance(response, dict):
            return f"HTTP response {index} is malformed"
        if any(marker in response for marker in ("error", "transport_error", "timeout", "timed_out")):
            return f"HTTP response {index} carries a transport/error diagnostic"
        status = response.get("status")
        if type(status) is not int or not 100 <= status <= 599:
            return f"HTTP response {index} has no valid integer HTTP status"
        if not isinstance(response.get("body"), str) or not isinstance(response.get("headers"), dict):
            return f"HTTP response {index} has no captured HTTP body or headers"
    return None


def observation_problems(record: Mapping[str, Any], manifest: Any, *, require_responses: bool = True) -> list[str]:
    """Check endpoint provenance and, by default, complete HTTP observations.

    Comparison and replay check provenance separately from completeness so
    that a one-sided observation loss remains a divergence and its diagnostic
    can travel in a truthful failing evidence package.
    """
    probes = [probe for probe in manifest.probes if probe.adapter == "http"]
    if not probes or record.get("status") != "observed":
        return []
    try:
        port = _port(record.get("http_service_port"))
        identities = record.get("http_request_identities")
        declarations = record.get("http_request_declarations")
        if not isinstance(identities, dict) or not isinstance(declarations, dict):
            raise ValueError("missing executed endpoint identities")
        for probe in probes:
            wanted = probe.params["requests"]
            if wanted == "$INPUT":
                item = next((item for item in manifest.input_domain.corpus if item.id == record.get("input_id")), None)
                wanted = item.input.get("requests") if item and isinstance(item.input, dict) else declarations.get(probe.id)
            observed_declaration = declarations.get(probe.id)
            if redact_value(wanted)[0] != observed_declaration:
                raise ValueError("executed request declaration differs from manifest input")
            expected = validate_requests(wanted, port)
            if redact_value(expected)[0] != identities.get(probe.id):
                raise ValueError("executed endpoint identity differs from declared HTTP request")
            responses = record.get("probes", {}).get(probe.id, {}).get("responses")
            if require_responses:
                problem = response_problem(record.get("probes", {}).get(probe.id), wanted)
                if problem:
                    raise ValueError(problem)
            if not isinstance(responses, list):
                responses = []
            if len(responses) > len(expected):
                raise ValueError("HTTP response count differs from declared requests")
            for req, endpoint, response in zip(wanted, expected, responses):
                echo = {"method": endpoint["method"], "path": endpoint["target"], "body": req.get("body")}
                if not isinstance(response, dict) or response.get("request") != redact_value(echo)[0]:
                    raise ValueError("HTTP response is bound to a different request")
    except (ValueError, TypeError, AttributeError, KeyError) as error:
        return [f"HTTP endpoint evidence invalid: {error}"]
    return []
