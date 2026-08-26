"""Questrade token chain manager — SPEC.md §4.1.

The rules, verbatim from the spec, all enforced here:
- refresh tokens are single-use: every exchange is serialized behind one lock;
- the new refresh token is committed to the DB BEFORE the new access token is
  used for anything, then mirrored to data/qt_token_backup.json;
- exchange endpoint: 30 s timeout, exactly one retry with the same refresh
  token on timeout/transport error; invalid_grant on that retry means the first
  attempt likely succeeded server-side and the response was lost — chain gone;
- invalid_grant anywhere → status "broken", surfaced via /api/health;
- token values are never logged.
"""

import asyncio
import json
import logging
import os
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import settings, utcnow
from ..models import QtToken

log = logging.getLogger(__name__)

LOGIN_URL = "https://login.questrade.com/oauth2/token"
EXCHANGE_TIMEOUT = 30.0
REFRESH_MARGIN = timedelta(seconds=60)


class TokenError(Exception):
    """Transient token problem (network, unexpected HTTP status). Chain intact."""


class TokenAbsent(TokenError):
    """No refresh token has been pasted yet."""


class TokenBroken(TokenError):
    """Chain lost (invalid_grant). User must paste a fresh manual token."""


class TokenManager:
    def __init__(
        self,
        sessions: async_sessionmaker,
        backup_path=None,
        login_url: str = LOGIN_URL,
    ):
        self._sessions = sessions
        self._backup_path = backup_path or settings.token_backup_path
        self._login_url = login_url
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=EXCHANGE_TIMEOUT)
        # In-memory copy of the current access credentials; populated only
        # after a successful, persisted exchange (or from the DB on first use).
        self._access_token: str | None = None
        self._api_server: str | None = None
        self._expires_at = None

    async def aclose(self):
        await self._http.aclose()

    # -- public ----------------------------------------------------------

    async def status(self) -> dict:
        """Status for /api/health and Settings. Never includes token values."""
        async with self._sessions() as session:
            row = await session.get(QtToken, 1)
        if row is None:
            return {"status": "absent", "api_server": None, "access_expires_at": None, "updated_at": None}
        return {
            "status": row.status,
            "api_server": row.api_server,
            "access_expires_at": row.access_expires_at.isoformat() if row.access_expires_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    async def set_refresh_token(self, refresh_token: str) -> None:
        """Paste from Settings. Validates by exchanging immediately; clears a
        broken state on success."""
        async with self._lock:
            await self._exchange_and_persist(refresh_token.strip())

    async def ensure_access(self) -> tuple[str, str]:
        """Return (access_token, api_server), refreshing when <60 s remain."""
        if self._valid():
            return self._access_token, self._api_server
        async with self._lock:
            if self._valid():  # another task refreshed while we waited
                return self._access_token, self._api_server
            await self._refresh_locked()
            return self._access_token, self._api_server

    async def refresh(self) -> None:
        """Unconditional rotation — startup sequence and token_keepalive job."""
        async with self._lock:
            await self._refresh_locked()

    async def force_refresh(self, used_access_token: str) -> None:
        """After a 401. Skips the exchange if another task already rotated the
        token out from under the caller."""
        async with self._lock:
            if self._access_token and self._access_token != used_access_token:
                return
            await self._refresh_locked()

    # -- internals (call only while holding self._lock) -------------------

    def _valid(self) -> bool:
        return bool(
            self._access_token
            and self._expires_at
            and self._expires_at - utcnow() > REFRESH_MARGIN
        )

    async def _refresh_locked(self) -> None:
        async with self._sessions() as session:
            row = await session.get(QtToken, 1)
        if row is not None and row.status == "broken":
            raise TokenBroken("token chain is broken — paste a fresh token in Settings")
        if row is None or row.status == "absent" or not row.refresh_token:
            raise TokenAbsent("no refresh token configured — paste one in Settings")
        await self._exchange_and_persist(row.refresh_token)

    async def _exchange_and_persist(self, refresh_token: str) -> None:
        params = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        timed_out = False
        try:
            resp = await self._http.post(self._login_url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            timed_out = True
            log.warning("token exchange transport failure (%s); retrying once with the same token", type(exc).__name__)
            try:
                resp = await self._http.post(self._login_url, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc2:
                # Exactly one retry — chain state unknown but not provably lost.
                raise TokenError(f"token exchange unreachable after retry ({type(exc2).__name__})") from exc2

        # Questrade returns a BARE "400 Bad Request" (no invalid_grant JSON) for a
        # consumed or invalid refresh token — any 400 here means the chain is lost.
        if resp.status_code == 400:
            if timed_out:
                detail = (
                    "exchange rejected on the retry after a timeout — the first exchange "
                    "likely succeeded server-side and its response was lost; the chain "
                    "is unrecoverable. Paste a fresh token in Settings."
                )
            else:
                detail = "refresh token rejected — the chain is lost. Paste a fresh token in Settings."
            await self._mark_broken()
            raise TokenBroken(detail)
        if resp.status_code != 200:
            # Transient (5xx, throttling, nightly maintenance, …): do NOT break
            # the chain. Error bodies carry no secrets — safe to surface.
            raise TokenError(f"token exchange failed with HTTP {resp.status_code}: {resp.text[:150]}")

        data = resp.json()
        expires_at = utcnow() + timedelta(seconds=int(data.get("expires_in", 1800)))
        api_server = str(data["api_server"]).rstrip("/")

        # Rule 1: persist the new refresh token BEFORE the access token is used.
        async with self._sessions() as session:
            row = await session.get(QtToken, 1)
            if row is None:
                row = QtToken(id=1)
                session.add(row)
            row.refresh_token = data["refresh_token"]
            row.access_token = data["access_token"]
            row.api_server = api_server
            row.access_expires_at = expires_at
            row.status = "ok"
            row.updated_at = utcnow()
            await session.commit()
        self._write_backup(data["refresh_token"])

        # Only now expose the credentials to callers.
        self._access_token = data["access_token"]
        self._api_server = api_server
        self._expires_at = expires_at
        log.info("token chain rotated; access expires at %s UTC", expires_at.isoformat(timespec="seconds"))

    async def _mark_broken(self) -> None:
        self._access_token = None
        self._expires_at = None
        async with self._sessions() as session:
            row = await session.get(QtToken, 1)
            if row is None:
                row = QtToken(id=1)
                session.add(row)
            row.status = "broken"
            row.access_token = None
            row.access_expires_at = None
            row.updated_at = utcnow()
            await session.commit()

    def _write_backup(self, refresh_token: str) -> None:
        try:
            self._backup_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._backup_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"refresh_token": refresh_token, "updated_at": utcnow().isoformat()}))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._backup_path)
        except OSError as exc:
            # Belt-and-suspenders copy only; the DB commit already happened.
            log.warning("could not write token backup file: %s", exc)
