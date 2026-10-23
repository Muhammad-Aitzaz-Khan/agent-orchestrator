"""The web app's protective layer: who is really asking, what the browser may do with our pages,
and how much anyone can send or try.

Each piece answers a common attack (see docs/SECURITY.md for the full list and the sources):

    client_ip         the visitor's real address for rate limits; a forged X-Forwarded-For
                      header can't make one person look like many
    SecurityHeaders   CSP with hashes of our own inline scripts, HSTS, no sniffing, no framing
                      by other sites, no referrer leaks, no camera or microphone
    BodyLimit         refuses oversized requests before they are read into memory
    RateLimit         a sliding-window limit per address for the endpoints that cost money
                      (the AI) or invite guessing (sign-in, free days)
"""
import base64
import hashlib
import os
import re
import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def client_ip(request: Request, public: bool) -> str:
    """The visitor's address. Behind a host's proxy (Render) the last X-Forwarded-For entry is
    the one the proxy added, so it can't be forged; earlier entries come from the client."""
    if public:
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        trusted = max(1, int(os.getenv("TRUSTED_PROXIES", "1") or 1))
        if len(hops) >= trusted:
            return hops[-trusted]
    return request.client.host if request.client else "unknown"


class RateLimit:
    """At most `limit` events per key in a sliding `window` of seconds."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.events: dict[str, list[float]] = defaultdict(list)
        self.lock = threading.Lock()

    def hit(self, key: str, message: str = "Too many requests. Please wait a little and try again.") -> None:
        now_s = time.monotonic()
        with self.lock:
            recent = [t for t in self.events[key] if now_s - t < self.window]
            if len(recent) >= self.limit:
                self.events[key] = recent
                raise HTTPException(429, message)
            recent.append(now_s)
            self.events[key] = recent
            if len(self.events) > 50_000:  # never let the table grow without bound
                self.events = defaultdict(list, {k: v for k, v in self.events.items() if v and now_s - v[-1] < self.window})


_INLINE_SCRIPT = re.compile(rb"<script(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")[^>]*>(.*?)</script>", re.DOTALL)


def script_hashes(html: bytes) -> list[str]:
    """CSP hashes of a page's own inline scripts, so only those scripts may run."""
    return [f"'sha256-{base64.b64encode(hashlib.sha256(body).digest()).decode()}'"
            for body in _INLINE_SCRIPT.findall(html)]


def content_security_policy(hashes: list[str]) -> str:
    scripts = " ".join(["'self'", *hashes])
    return "; ".join([
        "default-src 'self'",
        f"script-src {scripts}",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: blob: https://*.googleusercontent.com",
        "connect-src 'self'",
        "frame-src 'self'",
        "frame-ancestors 'self'",
        "worker-src 'self'",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
    ])


class SecurityHeaders:
    """Adds protective headers to every response, and a CSP to every HTML page."""

    def __init__(self, app: ASGIApp, public: bool):
        self.app, self.public = app, public

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start: Message | None = None
        chunks: list[bytes] = []
        private = scope.get("path", "").startswith(("/api/", "/auth/"))  # personal data: never cache

        async def capture(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                if not _is_html(message):
                    await send(_with_headers(message, self.public, None, private))
                return
            if start is None or not _is_html(start):
                await send(message)
                return
            # HTML: collect the body so the CSP can list the page's own inline scripts
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                body = b"".join(chunks)
                headers = [(k, v) for k, v in start["headers"] if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode()))
                await send(_with_headers({**start, "headers": headers}, self.public, script_hashes(body), private))
                await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, capture)


def _is_html(message: Message) -> bool:
    return any(k.lower() == b"content-type" and v.startswith(b"text/html") for k, v in message["headers"])


def _with_headers(message: Message, public: bool, hashes: list[str] | None, private: bool = False) -> Message:
    headers = list(message["headers"])
    have = {k.lower() for k, _ in headers}

    def add(name: str, value: str) -> None:
        if name.encode() not in have:
            headers.append((name.encode(), value.encode()))

    add("x-content-type-options", "nosniff")
    add("referrer-policy", "strict-origin-when-cross-origin")
    add("x-frame-options", "SAMEORIGIN")
    add("permissions-policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()")
    add("cross-origin-opener-policy", "same-origin")
    add("cross-origin-resource-policy", "same-origin")
    add("x-permitted-cross-domain-policies", "none")
    add("x-xss-protection", "0")  # the old browser filter caused more harm than good; CSP replaces it
    if private:
        add("cache-control", "no-store")
    if public:
        add("strict-transport-security", "max-age=63072000; includeSubDomains")
    if hashes is not None:
        add("content-security-policy", content_security_policy(hashes))
    return {**message, "headers": headers}


class BodyLimit:
    """Refuses a request whose body is larger than `limit` bytes, before reading it all."""

    def __init__(self, app: ASGIApp, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.limit:
            await _too_large(send)
            return
        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    raise HTTPException(413, "That is too large.")
            return message

        await self.app(scope, counted, send)


async def _too_large(send: Send) -> None:
    body = b'{"detail":"That is too large."}'
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
