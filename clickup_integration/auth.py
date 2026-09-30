from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event
from urllib.parse import parse_qs, quote, urlparse

import requests

from clickup_integration.config import ClickUpSettings


@dataclass(frozen=True)
class OAuthTokenResponse:
    access_token: str
    token_type: str


def build_authorization_url(settings: ClickUpSettings, *, state: str | None = None) -> str:
    encoded_redirect_uri = quote(settings.redirect_uri, safe="")
    url = (
        f"{settings.authorization_url}?client_id={settings.client_id}"
        f"&redirect_uri={encoded_redirect_uri}"
    )
    if state:
        url += f"&state={quote(state, safe='')}"
    return url


def exchange_code_for_token(
    settings: ClickUpSettings,
    *,
    code: str,
    timeout_seconds: int = 30,
) -> OAuthTokenResponse:
    response = requests.post(
        settings.token_url,
        json={
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "code": code,
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    return OAuthTokenResponse(
        access_token=payload["access_token"],
        token_type=payload.get("token_type", "Bearer"),
    )


def wait_for_oauth_callback(
    settings: ClickUpSettings,
    *,
    expected_state: str,
    timeout_seconds: int = 300,
) -> dict[str, str]:
    if not expected_state:
        raise ValueError("OAuth callback requires nonempty expected state.")
    parsed = urlparse(settings.redirect_uri)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("CLICKUP_REDIRECT_URI must be an http or https URL.")
    if not parsed.hostname or not parsed.port:
        raise ValueError(
            "Local callback helper requires CLICKUP_REDIRECT_URI to include an explicit host and port."
        )

    result: dict[str, str] = {}
    done = Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            nonlocal result
            if urlparse(self.path).path == (parsed.path or "/"):
                query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                states = query.get("state", [])
                codes = query.get("code", [])
                if len(states) != 1 or not secrets.compare_digest(states[0].encode(), expected_state.encode()):
                    self._respond(400, "Invalid OAuth state.")
                    return
                if len(codes) == 1 and codes[0]:
                    result = {"code": query["code"][0]}
                    if "state" in query:
                        result["state"] = query["state"][0]
                    self._respond(
                        200,
                        (
                            "ClickUp authorization received. You can close this tab and return "
                            "to the terminal."
                        ),
                    )
                    done.set()
                    return

                self._respond(400, "No authorization code was provided by ClickUp.")
                return

            self._respond(404, "Not found.")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def _respond(self, status_code: int, message: str) -> None:
            body = message.encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer((parsed.hostname, parsed.port), CallbackHandler)
    server.timeout = 0.5
    deadline = time.monotonic() + timeout_seconds
    try:
        while not done.is_set():
            server.handle_request()
            if not done.is_set() and time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for the ClickUp OAuth callback.")
    finally:
        server.server_close()

    return result


def format_env_update(token: OAuthTokenResponse) -> str:
    return json.dumps(
        {
            "CLICKUP_ACCESS_TOKEN": token.access_token,
            "CLICKUP_TOKEN_TYPE": token.token_type,
        },
        indent=2,
        sort_keys=True,
    )
