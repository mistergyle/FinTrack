"""Abruf über die comdirect REST-API als Hintergrund-Job (gleiche Job-Mechanik wie FinTS)."""
from __future__ import annotations

import logging
import os
import threading
from datetime import date, timedelta
from typing import Any, Callable

from . import fints_sync  # noqa: F401  (Tests greifen über comdirect_sync.fints_sync auf submit_tan zu)
from .comdirect_api import ComdirectClient, ComdirectError, tx_to_parsed
from .db import now_iso, session
from .fints_sync import JOBS, _LOCK, _purge, _wait_for_user, Job
from .importers import store_transactions
import uuid


log = logging.getLogger("uvicorn.error").getChild("comdirect")


def _run(job: Job, account: dict, pin: str, days: int, factory: Callable[..., Any]) -> None:
    client = None
    try:
        log.info("Abruf gestartet (Konto %s, %s Tage)", account["id"], days)
        client = factory(os.environ["COMDIRECT_CLIENT_ID"], os.environ["COMDIRECT_CLIENT_SECRET"])
        ch = client.login(account["fints_user"], pin, account.get("fints_tan_mechanism") or None)
        job.challenge_image = ch.image_data_uri
        if ch.is_push:
            _wait_for_user(job, "need_approval", ch.text, None)
            tan = None
        else:
            tan = _wait_for_user(job, "need_tan", ch.text, None)
        log.info("Anmeldung ok, TAN-Typ: %s – aktiviere Sitzung", "Push" if ch.is_push else "TAN")
        client.activate(ch, tan or None)
        job.message = "Konten werden abgefragt …"
        accounts = client.accounts()
        if not accounts:
            raise ComdirectError("comdirect hat keine Konten geliefert.")
        target = accounts[0]
        if account.get("iban"):
            want = account["iban"].replace(" ", "").upper()
            target = next((a for a in accounts if a["iban"] == want), None)
            if target is None:
                raise ComdirectError("IBAN nicht gefunden. Vorhanden: " + ", ".join(a["iban"] or a["id"] for a in accounts))
        elif len(accounts) > 1:
            raise ComdirectError("Mehrere Konten vorhanden – bitte beim Konto die IBAN eintragen: "
                                 + ", ".join(a["iban"] or a["id"] for a in accounts))
        job.message = "Umsätze werden geladen …"
        log.info("Lade Umsätze für Konto-ID %s", target["id"])
        raw = client.transactions(target["id"], date.today() - timedelta(days=days))
        parsed = [p for p in (tx_to_parsed(v) for v in raw) if p]
        with session() as conn:
            stats = store_transactions(conn, account["id"], parsed, fuzzy=True)
            conn.execute("UPDATE accounts SET last_sync=?, iban=COALESCE(iban, NULLIF(?, '')) WHERE id=?",
                         (now_iso(), target["iban"], account["id"]))
        job.result, job.state, job.message = stats, "done", "Fertig."
        log.info("Abruf fertig: %s", stats)
    except Exception as e:  # noqa: BLE001
        msg = str(e).replace(pin, "***") if pin else str(e)
        job.state, job.message = "error", f"{type(e).__name__}: {msg}"
        log.error("Abruf fehlgeschlagen: %s", job.message)
    finally:
        if client is not None and getattr(client, "access", None):
            client.revoke()


def start_sync(account: dict, pin: str, days: int = 90,
               client_factory: Callable[..., Any] | None = None) -> Job:
    if not account.get("fints_user"):
        raise ValueError("Für dieses Konto fehlt die Zugangsnummer.")
    with _LOCK:
        _purge()
        job = Job(id=uuid.uuid4().hex, account_id=account["id"], message="Anmeldung bei comdirect …")
        JOBS[job.id] = job
    threading.Thread(target=_run, args=(job, account, pin, days, client_factory or ComdirectClient),
                     daemon=True).start()
    return job
