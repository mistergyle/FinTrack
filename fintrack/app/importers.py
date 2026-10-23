"""CSV-Import für ING und comdirect (Umsatzexport aus dem Online-Banking)
sowie gemeinsame Speicherlogik (Deduplizierung + Kategorisierung)."""
from __future__ import annotations

import csv
import hashlib
import io
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .categorizer import Categorizer
from .db import now_iso


@dataclass
class ParsedTx:
    booking_date: str                    # ISO
    value_date: str | None
    payee: str
    purpose: str
    booking_text: str
    amount_cents: int
    currency: str = "EUR"
    counterparty_iban: str | None = None


@dataclass
class ParsedFile:
    bank: str                            # ing | comdirect
    iban: str | None
    transactions: list[ParsedTx] = field(default_factory=list)
    holder: str | None = None            # Kontoinhaber, falls die Datei ihn nennt (ING: "Kunde")


class ImportError_(ValueError):
    pass


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ImportError_("Datei-Kodierung nicht lesbar")


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()


def parse_amount(s: str) -> int:
    s = clean(s).replace("€", "").replace("EUR", "").replace(" ", "")
    if not s:
        raise ValueError("leerer Betrag")
    s = s.replace(".", "").replace(",", ".")
    try:
        return int((Decimal(s) * 100).to_integral_value())
    except InvalidOperation as e:
        raise ValueError(f"Betrag nicht lesbar: {s}") from e


def parse_date(s: str) -> str | None:
    s = clean(s)
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None  # z. B. "offen" bei vorgemerkten Umsätzen


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text), delimiter=";", quotechar='"'))


def detect_bank(rows: list[list[str]]) -> str | None:
    for row in rows:
        cells = [clean(c) for c in row]
        if "Buchung" in cells and "Betrag" in cells:
            return "ing"
        if "Buchungstag" in cells and any(c.startswith("Umsatz in") for c in cells):
            return "comdirect"
    return None


def _parse_ing(rows: list[list[str]]) -> ParsedFile:
    iban = None
    holder = None
    header_idx = None
    for i, row in enumerate(rows):
        cells = [clean(c) for c in row]
        if cells and cells[0].upper() == "IBAN" and len(cells) > 1:
            iban = cells[1]
        if cells and cells[0] == "Kunde" and len(cells) > 1 and cells[1]:
            holder = cells[1]
        if "Buchung" in cells and "Betrag" in cells:
            header_idx = i
            break
    if header_idx is None:
        raise ImportError_("ING-Kopfzeile nicht gefunden")
    header = [clean(c) for c in rows[header_idx]]
    col: dict[str, int] = {}
    for i, name in enumerate(header):
        if name and name != "Währung":      # "Währung" steht zweimal (Saldo, Betrag)
            col.setdefault(name, i)
    # "Währung" kommt zweimal vor (Saldo, Betrag) -> Währung des Betrags = Spalte nach "Betrag"
    i_amount = header.index("Betrag")
    i_cur = i_amount + 1 if i_amount + 1 < len(header) and header[i_amount + 1] == "Währung" else None

    def get(row, name):
        i = col.get(name)
        return row[i] if i is not None and i < len(row) else ""

    pf = ParsedFile("ing", iban, holder=holder)
    for row in rows[header_idx + 1:]:
        if not row or len(row) <= i_amount or not clean(row[0]):
            continue
        d = parse_date(get(row, "Buchung"))
        if not d:
            continue
        try:
            cents = parse_amount(row[i_amount])
        except ValueError:
            continue
        pf.transactions.append(ParsedTx(
            booking_date=d,
            value_date=parse_date(get(row, "Valuta")),
            payee=clean(get(row, "Auftraggeber/Empfänger")),
            purpose=clean(get(row, "Verwendungszweck")),
            booking_text=clean(get(row, "Buchungstext")),
            amount_cents=cents,
            currency=clean(row[i_cur]) if i_cur is not None and i_cur < len(row) and clean(row[i_cur]) else "EUR",
        ))
    return pf


_CD_PAYEE = re.compile(
    r"^(?:Auftraggeber|Empfänger|Zahlungspflichtiger|Zahlungsempfänger):\s*(?P<payee>.*?)"
    r"(?:\s+Buchungstext:\s*(?P<purpose>.*?))?(?:\s+Ref\.\s*(?P<ref>\S+.*))?$",
    re.IGNORECASE | re.DOTALL,
)


def _parse_comdirect(rows: list[list[str]]) -> ParsedFile:
    header_idx = None
    for i, row in enumerate(rows):
        cells = [clean(c) for c in row]
        if "Buchungstag" in cells and any(c.startswith("Umsatz in") for c in cells):
            header_idx = i
            break
    if header_idx is None:
        raise ImportError_("comdirect-Kopfzeile nicht gefunden")
    header = [clean(c) for c in rows[header_idx]]
    idx = {name: i for i, name in enumerate(header) if name}
    i_amount = next(i for n, i in idx.items() if n.startswith("Umsatz in"))
    i_val = next((i for n, i in idx.items() if n.startswith("Wertstellung")), None)

    pf = ParsedFile("comdirect", None)
    for row in rows[header_idx + 1:]:
        if len(row) <= i_amount:
            continue
        d = parse_date(row[idx["Buchungstag"]])
        if not d:
            continue  # "offen" (vorgemerkt) und Summenzeilen überspringen
        try:
            cents = parse_amount(row[i_amount])
        except ValueError:
            continue
        text = clean(row[idx["Buchungstext"]]) if "Buchungstext" in idx else ""
        payee, purpose = "", text
        m = _CD_PAYEE.match(text)
        if m:
            payee = clean(m.group("payee"))
            purpose = clean(m.group("purpose") or "")
        pf.transactions.append(ParsedTx(
            booking_date=d,
            value_date=parse_date(row[i_val]) if i_val is not None and i_val < len(row) else None,
            payee=payee,
            purpose=purpose,
            booking_text=clean(row[idx["Vorgang"]]) if "Vorgang" in idx else "",
            amount_cents=cents,
        ))
    return pf


def parse_csv(data: bytes) -> ParsedFile:
    rows = _rows(decode_bytes(data))
    bank = detect_bank(rows)
    if bank == "ing":
        return _parse_ing(rows)
    if bank == "comdirect":
        return _parse_comdirect(rows)
    raise ImportError_(
        "Format nicht erkannt. Unterstützt: ING-CSV (Umsatzanzeige → Export) und comdirect-CSV (Umsätze → CSV)."
    )


def tx_hash(account_id: int, t: ParsedTx, n: int) -> str:
    key = "|".join([str(account_id), t.booking_date, str(t.amount_cents), t.payee.lower(),
                    t.purpose.lower(), t.booking_text.lower(), str(n)])
    return hashlib.sha1(key.encode()).hexdigest()


SAME_BOOKING = 0.7      # Ähnlichkeit ab der zwei Buchungen (gleiches Datum, gleicher Betrag) als dieselbe gelten


_GENERIC = {"visa", "gmbh", "mastercard", "girocard", "sepa", "kartenzahlung", "lastschrift", "ueberweisung",
            "überweisung", "einkauf", "bank", "shop", "store", "markt", "berlin", "hamburg", "muenchen", "münchen"}


def _norm(*parts: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]", "", " ".join(parts).lower())


def similarity(payee_a: str, purpose_a: str, payee_b: str, purpose_b: str) -> float:
    """0..1 – Vergleich ohne Leerzeichen/Satzzeichen, denn FinTS und CSV trennen Zeilen unterschiedlich."""
    from difflib import SequenceMatcher
    pa, pb = _norm(payee_a), _norm(payee_b)
    a, b = _norm(payee_a, purpose_a)[:160], _norm(payee_b, purpose_b)[:160]
    if not a or not b:
        return 0.0
    r = SequenceMatcher(None, a, b, autojunk=False).ratio()
    if pa and pb and (pa == pb or pa.startswith(pb) or pb.startswith(pa)):
        r = max(r, 0.5 + 0.5 * min(1.0, SequenceMatcher(None, _norm(purpose_a)[:120], _norm(purpose_b)[:120]).ratio() + 0.3))
    ta = {w for w in re.findall(r"[a-z0-9äöüß]{4,}", payee_a.lower())} - _GENERIC
    tb = {w for w in re.findall(r"[a-z0-9äöüß]{4,}", payee_b.lower())} - _GENERIC
    if ta & tb:                                   # gemeinsamer Namensbestandteil, z. B. „netflix“
        r = max(r, SAME_BOOKING)
    return r


def remove_duplicates(conn: sqlite3.Connection) -> int:
    """Entfernt Dubletten, die durch Mischen von FinTS/API und CSV entstanden sind.

    Gleiche Buchungen aus derselben Quelle (identischer Rohtext, z. B. zwei Kaffee am selben Tag)
    bleiben erhalten; nur Zeilen mit *abweichendem* Text, aber hoher Ähnlichkeit gelten als Dublette."""
    rows = conn.execute("SELECT id,account_id,booking_date,amount_cents,payee,purpose,booking_text,category_source "
                        "FROM transactions ORDER BY id").fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["account_id"], r["booking_date"], r["amount_cents"]), []).append(r)
    drop: list[int] = []
    for grp in groups.values():
        if len(grp) < 2:
            continue
        classes: dict[tuple, list] = {}
        for r in grp:
            classes.setdefault((r["payee"].lower(), r["purpose"].lower(), r["booking_text"].lower()), []).append(r)
        keys = list(classes)
        gone: set[tuple] = set()
        for i, ka in enumerate(keys):
            for kb in keys[i + 1:]:
                if ka in gone or kb in gone:
                    continue
                A, B = classes[ka], classes[kb]
                if similarity(A[0]["payee"], A[0]["purpose"], B[0]["payee"], B[0]["purpose"]) < SAME_BOOKING:
                    continue
                keep, kill = (A, B) if not any(x["category_source"] == "manual" for x in B) or any(
                    x["category_source"] == "manual" for x in A) else (B, A)
                n = min(len(A), len(B))
                victims = sorted(kill, key=lambda x: -x["id"])[:n]
                drop += [v["id"] for v in victims]
                if n == len(kill):
                    gone.add(ka if kill is A else kb)
                else:
                    classes[ka if kill is A else kb] = [x for x in kill if x not in victims]
    for i in drop:
        conn.execute("DELETE FROM transactions WHERE id=?", (i,))
    return len(drop)


def store_transactions(conn: sqlite3.Connection, account_id: int,
                       txs: list[ParsedTx], fuzzy: bool = False) -> dict:
    """Speichert Umsätze, überspringt Duplikate, kategorisiert neue Umsätze.

    Identische Umsätze am selben Tag (gleicher Betrag/Text) bekommen einen Zähler,
    damit sie nicht als Duplikat verworfen werden – ein erneuter Import derselben
    Quelle bleibt trotzdem idempotent.

    fuzzy=True (für API/FinTS-Abrufe): Dieselbe Buchung kann aus einer anderen Quelle
    (z. B. CSV) mit leicht anderem Text vorliegen. Pro (Datum, Betrag) werden dann nur so
    viele neue Umsätze angelegt, wie über die bereits vorhandenen hinausgehen."""
    cz = Categorizer(conn)
    seen: Counter = Counter()
    items: list[tuple[ParsedTx, str]] = []
    for t in txs:
        base = (t.booking_date, t.amount_cents, t.payee.lower(), t.purpose.lower(), t.booking_text.lower())
        n = seen[base]
        seen[base] += 1
        items.append((t, tx_hash(account_id, t, n)))

    skip: set[str] = set()
    if fuzzy:
        groups: dict[tuple, list[tuple[ParsedTx, str]]] = {}
        for t, h in items:
            groups.setdefault((t.booking_date, t.amount_cents), []).append((t, h))
        for (d, a), grp in groups.items():
            existing = {r["hash"]: r for r in conn.execute(
                "SELECT hash,payee,purpose,booking_text FROM transactions "
                "WHERE account_id=? AND booking_date=? AND amount_cents=?", (account_id, d, a))}
            used = {h for _, h in grp if h in existing}            # exakte Treffer verbrauchen ihren Bestand
            pool = [r for h, r in existing.items() if h not in used]
            for t, h in grp:
                if h in existing or not pool:
                    continue
                best = max(pool, key=lambda r: similarity(t.payee, t.purpose, r["payee"], r["purpose"]))
                if similarity(t.payee, t.purpose, best["payee"], best["purpose"]) >= SAME_BOOKING:
                    pool.remove(best)
                    skip.add(h)

    added = skipped = 0
    ts = now_iso()                      # ein Zeitstempel je Import-/Abruf-Lauf (für die „neu“-Markierung)
    for t, h in items:
        if h in skip:
            skipped += 1
            continue
        cat = cz.categorize(t.payee, t.purpose, t.booking_text, t.amount_cents, t.counterparty_iban)
        cur = conn.execute(
            "INSERT OR IGNORE INTO transactions(account_id,booking_date,value_date,payee,purpose,"
            "booking_text,counterparty_iban,amount_cents,currency,category_id,category_source,hash,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,'rule',?,?)",
            (account_id, t.booking_date, t.value_date, t.payee, t.purpose, t.booking_text,
             t.counterparty_iban, t.amount_cents, t.currency, cat, h, ts),
        )
        if cur.rowcount:
            added += 1
        else:
            skipped += 1
    dates = [t.booking_date for t in txs]
    return {"added": added, "duplicates": skipped,
            "from": min(dates) if dates else None, "to": max(dates) if dates else None}


def find_account_by_iban(conn: sqlite3.Connection, iban: str | None) -> int | None:
    if not iban:
        return None
    norm = re.sub(r"\s+", "", iban).upper()
    for r in conn.execute("SELECT id,iban FROM accounts WHERE iban IS NOT NULL"):
        if re.sub(r"\s+", "", r["iban"]).upper() == norm:
            return r["id"]
    return None
