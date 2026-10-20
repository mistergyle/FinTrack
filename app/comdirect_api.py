"""Client für die comdirect REST-API (developer.comdirect.de).

Ablauf (nach öffentlicher Doku/Wrappern, Stand 2020 – NICHT gegen die Live-API verifiziert):
 1. OAuth2 Password-Grant  (client_id/secret + Zugangsnummer + PIN)      -> Access-Token
 2. Session abfragen, TAN-Challenge anfordern ("validate")                 -> push/photo/mTAN
 3. Challenge bestätigen (PATCH, ggf. mit TAN im Header)                   -> Session aktiv
 4. OAuth2 "cd_secondary" tauschen                                         -> Token mit Banking-Rechten
 5. Konten (balances) und Umsätze (transactions) lesen (nur lesend!)

Nur Standardbibliothek (urllib), die HTTP-Schicht ist austauschbar (Tests).
"""
from __future__ import annotations

import json
import os
import random
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from .importers import ParsedTx, clean

API = "https://api.comdirect.de/api"
OAUTH = "https://api.comdirect.de"
TIMEOUT = 30

HttpFn = Callable[[str, str, dict, bytes | None], tuple[int, dict, bytes]]


class ComdirectError(Exception):
    pass


def configured() -> bool:
    return bool(os.environ.get("COMDIRECT_CLIENT_ID") and os.environ.get("COMDIRECT_CLIENT_SECRET"))


def default_http(method: str, url: str, headers: dict, data: bytes | None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
    except urllib.error.URLError as e:
        raise ComdirectError(f"Verbindung zu comdirect fehlgeschlagen: {e.reason}") from e


@dataclass
class Challenge:
    id: str
    typ: str
    challenge: str | None
    available: list

    @property
    def is_push(self) -> bool:
        return self.typ.upper().endswith("PUSH")

    @property
    def image_data_uri(self) -> str | None:
        if self.typ.upper() == "P_TAN" and self.challenge:
            return "data:image/png;base64," + self.challenge
        return None

    @property
    def text(self) -> str:
        if self.is_push:
            return "Bitte die Anmeldung in der comdirect-App (pushTAN) freigeben."
        if self.typ.upper() == "P_TAN":
            return "Bitte das photoTAN-Bild mit der photoTAN-App scannen und die TAN eingeben."
        return clean(self.challenge or "") or "Bitte die TAN eingeben."


class ComdirectClient:
    def __init__(self, client_id: str, client_secret: str, http: HttpFn | None = None):
        self.client_id, self.client_secret = client_id, client_secret
        self.http = http or default_http
        self.access = None
        self.refresh = None
        self.session_id = None
        self.session_uuid = "".join(random.choices(string.ascii_letters + string.digits, k=32))

    def _info_header(self) -> str:
        return json.dumps({"clientRequestId": {
            "sessionId": self.session_uuid[:32],
            "requestId": f"{int(time.time() * 1000) % 1000000000:09d}"}})

    def _call(self, method: str, url: str, *, body: Any = None, form: dict | None = None,
              extra: dict | None = None, expect: tuple = (200,)) -> tuple[dict, bytes]:
        headers = {"Accept": "application/json", "x-http-request-info": self._info_header()}
        data = None
        if form:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = urllib.parse.urlencode(form).encode()
        elif body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if self.access and not url.endswith("/oauth/token"):
            headers["Authorization"] = f"Bearer {self.access}"
        headers.update(extra or {})
        status, rh, rb = self.http(method, url, headers, data)
        if status not in expect:
            info = rh.get("x-http-response-info", "")
            detail = ""
            try:
                msgs = json.loads(info).get("messages") or []
                detail = "; ".join(m.get("message", "") for m in msgs if isinstance(m, dict))
            except Exception:  # noqa: BLE001
                detail = clean(rb.decode("utf-8", "replace"))[:200]
            raise ComdirectError(f"comdirect antwortet mit HTTP {status}" + (f": {detail}" if detail else ""))
        return rh, rb

    def _token(self, **fields) -> None:
        _, rb = self._call("POST", f"{OAUTH}/oauth/token",
                           form={"client_id": self.client_id, "client_secret": self.client_secret, **fields})
        d = json.loads(rb)
        self.access, self.refresh = d["access_token"], d.get("refresh_token")

    def login(self, user: str, pin: str, tan_type: str | None = None) -> Challenge:
        self._token(grant_type="password", username=user, password=pin)
        _, rb = self._call("GET", f"{API}/session/clients/user/v1/sessions")
        sessions = json.loads(rb)
        if not sessions:
            raise ComdirectError("Keine Session von comdirect erhalten.")
        self.session_id = sessions[0]["identifier"]
        extra = {"x-once-authentication-info": json.dumps({"typ": tan_type})} if tan_type else None
        rh, _ = self._call(
            "POST", f"{API}/session/clients/user/v1/sessions/{self.session_id}/validate",
            body={"identifier": self.session_id, "sessionTanActive": True, "activated2FA": True},
            extra=extra, expect=(200, 201))
        info = json.loads(rh.get("x-once-authentication-info") or "{}")
        if not info.get("id"):
            raise ComdirectError("Keine TAN-Challenge von comdirect erhalten.")
        return Challenge(info["id"], info.get("typ", ""), info.get("challenge"),
                         info.get("availableTypes") or [])

    def activate(self, ch: Challenge, tan: str | None) -> None:
        extra = {"x-once-authentication-info": json.dumps({"id": ch.id})}
        if tan:
            extra["x-once-authentication"] = tan
        self._call("PATCH", f"{API}/session/clients/user/v1/sessions/{self.session_id}",
                   body={"identifier": self.session_id, "sessionTanActive": True, "activated2FA": True},
                   extra=extra)
        self._token(grant_type="cd_secondary", token=self.access)

    def revoke(self) -> None:
        try:
            self._call("DELETE", f"{OAUTH}/oauth/revoke", expect=(200, 204))
        except ComdirectError:
            return None

    def accounts(self) -> list[dict]:
        _, rb = self._call("GET", f"{API}/banking/clients/user/v2/accounts/balances")
        out = []
        for v in json.loads(rb).get("values", []):
            acc = v.get("account") or {}
            aid = v.get("accountId") or acc.get("accountId")
            if not aid:
                continue
            out.append({"id": aid,
                        "iban": (acc.get("iban") or "").replace(" ", "").upper(),
                        "type": (acc.get("accountType") or {}).get("text", "")})
        return out

    def transactions(self, account_id: str, since: date, page: int = 100, max_pages: int = 200) -> list[dict]:
        out: list[dict] = []
        first = 0
        for _ in range(max_pages):
            q = urllib.parse.urlencode({"transactionState": "BOOKED", "paging-first": first,
                                        "paging-count": page, "min-bookingDate": since.isoformat()})
            _, rb = self._call("GET", f"{API}/banking/v1/accounts/{account_id}/transactions?{q}")
            d = json.loads(rb)
            vals = d.get("values", [])
            out.extend(vals)
            matches = (d.get("paging") or {}).get("matches")
            first += len(vals)
            # Die API liefert pro Seite oft weniger als `paging-count` (laut Wrappern max. 20).
            # Darum NICHT bei "kurzer Seite" abbrechen, sondern bei leerer Seite oder wenn alle
            # Treffer (paging.matches) geholt sind.
            if not vals or (matches is not None and first >= matches):
                break
        return out


def _holder(v: dict, *keys: str) -> str:
    for k in keys:
        h = v.get(k)
        if not isinstance(h, dict) or not h.get("holderName"):
            continue
        return clean(h["holderName"])
    return ""


def tx_to_parsed(v: dict) -> ParsedTx | None:
    """Wandelt einen comdirect-Umsatz (JSON) in ParsedTx um. Fehlt Datum/Betrag -> None."""
    booking = v.get("bookingDate")
    amt = (v.get("amount") or {}).get("value")
    if not booking or amt is None:
        return None
    cents = int((Decimal(str(amt)) * 100).to_integral_value())
    payee = _holder(v, "remitter", "deptor") if cents > 0 else (
        _holder(v, "creditor") or _holder(v, "creditor", "remitter", "deptor"))
    return ParsedTx(
        booking_date=booking,
        value_date=v.get("valutaDate"),
        payee=payee,
        purpose=clean(v.get("remittanceInfo")),
        booking_text=clean((v.get("transactionType") or {}).get("text")),
        amount_cents=cents,
        currency=(v.get("amount") or {}).get("unit") or "EUR",
    )
