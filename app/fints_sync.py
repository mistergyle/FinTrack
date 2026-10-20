"""Direkte Kontoanbindung per FinTS/HBCI (PIN/TAN) über die Bibliothek `fints`.

Wichtig:
 * Die PIN wird nie gespeichert – sie lebt nur im Arbeitsspeicher des laufenden Sync-Jobs.
 * Für FinTS braucht man eine bei der Deutschen Kreditwirtschaft registrierte Produkt-ID
   (Umgebungsvariable FINTS_PRODUCT_ID, siehe README).
 * Die TAN-Abfrage läuft asynchron: Der Job meldet "need_tan" bzw. "need_approval"
   (pushTAN-Freigabe in der Banking-App), die Oberfläche fragt den Status per Polling ab.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable

from .db import now_iso, session
from .importers import ParsedTx, clean, store_transactions

BANK_PRESETS = {
    "ing": {"blz": "50010517", "url": "https://fints.ing.de/fints/"},
    "comdirect": {"blz": "20041155", "url": "https://fints.comdirect.de/fints"},
}

TAN_TIMEOUT_S = 300


@dataclass
class Job:
    id: str
    account_id: int
    state: str = "running"      # running | need_tan | need_approval | done | error
    message: str = "Verbindung wird aufgebaut …"
    challenge: str | None = None
    challenge_image: str | None = None      # data:image/png;base64,… (photoTAN)
    result: dict | None = None
    _tan: str | None = None
    _event: threading.Event = field(default_factory=threading.Event)
    created: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {"id": self.id, "account_id": self.account_id, "state": self.state,
                "message": self.message, "challenge": self.challenge,
                "challenge_image": self.challenge_image, "result": self.result}


JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def fints_tx_to_parsed(tx: Any) -> ParsedTx | None:
    """Wandelt ein mt940-Transaction-Objekt (python-fints) in ParsedTx um."""
    d = tx.data if hasattr(tx, "data") else tx
    amt = d.get("amount")
    if amt is None:
        return None
    value = Decimal(str(getattr(amt, "amount", amt)))
    status = (d.get("status") or "").upper()
    if status in ("D", "RC"):        # Soll bzw. Storno einer Gutschrift -> Abfluss
        value = -abs(value)
    elif status in ("C", "RD"):      # Haben bzw. Storno einer Belastung -> Zufluss
        value = abs(value)
    booking = d.get("date") or d.get("entry_date")
    if booking is None:
        return None
    val = d.get("entry_date")
    return ParsedTx(
        booking_date=booking.isoformat(),
        value_date=val.isoformat() if val else None,
        payee=clean(d.get("applicant_name")),
        purpose=clean(d.get("purpose")),
        booking_text=clean(d.get("posting_text")),
        amount_cents=int((value * 100).to_integral_value()),
        currency=getattr(amt, "currency", None) or d.get("currency") or "EUR",
        counterparty_iban=clean(d.get("applicant_iban")) or None,
    )


def _wait_for_user(job: Job, state: str, message: str, challenge: str | None) -> str:
    job.state, job.message, job.challenge = state, message, challenge
    job._event.clear()
    if not job._event.wait(TAN_TIMEOUT_S):
        raise TimeoutError("Keine TAN-Eingabe/Freigabe innerhalb von 5 Minuten.")
    job.state, job.challenge, job.challenge_image = "running", None, None
    return job._tan or ""


def _challenge_text(resp: Any) -> str:
    return clean(getattr(resp, "challenge", "") or "") or "Bitte TAN eingeben."


def _drive_tan(job: Job, client: Any, resp: Any) -> Any:
    """Führt TAN-Dialoge (klassisch und decoupled/pushTAN) bis zum Ergebnis."""
    while hasattr(resp, "challenge") and type(resp).__name__.startswith("NeedTAN"):
        if getattr(resp, "decoupled", False):
            _wait_for_user(job, "need_approval",
                           "Bitte die Anmeldung in deiner Banking-App freigeben, dann bestätigen.",
                           _challenge_text(resp))
            resp = client.send_tan(resp, "")
        else:
            tan = _wait_for_user(job, "need_tan", "Bitte TAN eingeben.", _challenge_text(resp))
            resp = client.send_tan(resp, tan)
    return resp


def _setup_tan_mechanism(client: Any, preferred: str | None) -> None:
    """Wählt ein TAN-Verfahren (bevorzugt: gespeichertes, sonst pushTAN/decoupled, sonst erstes)."""
    client.fetch_tan_mechanisms()
    mechs = client.get_tan_mechanisms()          # {code: TANMechanism}
    if not mechs:
        return
    if preferred and preferred in mechs:
        client.set_tan_mechanism(preferred)
        return
    if len(mechs) == 1:
        client.set_tan_mechanism(next(iter(mechs)))
        return
    for code, m in mechs.items():
        name = (getattr(m, "name", "") or "").lower()
        if "push" in name or "decoupled" in name or "app" in name:
            client.set_tan_mechanism(code)
            return
    client.set_tan_mechanism(next(iter(mechs)))


def _run(job: Job, account: dict, pin: str, days: int, client_factory: Callable[..., Any]) -> None:
    try:
        client = client_factory(
            account["blz"], account["fints_user"], pin, account["fints_url"],
            product_id=os.environ.get("FINTS_PRODUCT_ID", ""),
        )
        _setup_tan_mechanism(client, account.get("fints_tan_mechanism"))
        with client:
            if getattr(client, "init_tan_response", None):
                _drive_tan(job, client, client.init_tan_response)
            job.message = "Konten werden abgefragt …"
            sepa = client.get_sepa_accounts()
            if not sepa:
                raise RuntimeError("Die Bank hat keine Konten geliefert.")
            target = sepa[0]
            if account.get("iban"):
                want = account["iban"].replace(" ", "").upper()
                target = next((a for a in sepa if (a.iban or "").upper() == want), None)
                if target is None:
                    raise RuntimeError(
                        "IBAN nicht im Bankzugang gefunden. Vorhanden: " + ", ".join(a.iban for a in sepa))
            start = date.today() - timedelta(days=days)
            job.message = "Umsätze werden geladen …"
            res = client.get_transactions(target, start, date.today())
            res = _drive_tan(job, client, res)
            parsed = [p for p in (fints_tx_to_parsed(t) for t in res) if p]
        with session() as conn:
            stats = store_transactions(conn, account["id"], parsed, fuzzy=True)
            conn.execute("UPDATE accounts SET last_sync=?, iban=COALESCE(iban,?) WHERE id=?",
                         (now_iso(), getattr(target, "iban", None), account["id"]))
        job.result, job.state, job.message = stats, "done", "Fertig."
    except Exception as e:  # noqa: BLE001 – Fehler dem Nutzer anzeigen, PIN nie ausgeben
        msg = str(e).replace(pin, "***") if pin else str(e)
        job.state, job.message = "error", f"{type(e).__name__}: {msg}"


def _default_factory(*args, **kwargs):
    try:
        from fints.client import FinTS3PinTanClient
    except ImportError as e:
        raise RuntimeError("Paket 'fints' nicht installiert (pip install fints).") from e
    if not kwargs.get("product_id"):
        raise RuntimeError(
            "FINTS_PRODUCT_ID fehlt. Für FinTS ist eine registrierte Produkt-ID nötig – siehe README.")
    return FinTS3PinTanClient(*args, **kwargs)


def start_sync(account: dict, pin: str, days: int = 90,
               client_factory: Callable[..., Any] | None = None) -> Job:
    if not (account.get("blz") and account.get("fints_url") and account.get("fints_user")):
        raise ValueError("Für dieses Konto sind BLZ, FinTS-URL und Zugangsnummer nötig.")
    with _LOCK:
        _purge()
        job = Job(id=uuid.uuid4().hex, account_id=account["id"])
        JOBS[job.id] = job
    t = threading.Thread(target=_run, args=(job, account, pin, days, client_factory or _default_factory),
                         daemon=True)
    t.start()
    return job


def submit_tan(job_id: str, tan: str = "") -> Job:
    job = JOBS[job_id]
    if job.state not in ("need_tan", "need_approval"):
        raise ValueError("Der Job wartet gerade nicht auf eine TAN/Freigabe.")
    job._tan = tan.strip()
    job._event.set()
    return job


def _purge() -> None:
    cutoff = time.time() - 3600
    for k in [k for k, j in JOBS.items() if j.created < cutoff]:
        JOBS.pop(k, None)
