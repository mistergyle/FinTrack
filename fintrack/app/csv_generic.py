"""Generischer CSV-Import mit Spalten-Zuordnung (Kontotyp „Andere“).

Der Nutzer ordnet einmalig die Spalten seiner Bank-CSV den Feldern der App zu. Die Zuordnung
(`mapping`) wird am Konto gespeichert und bei späteren Importen anhand der Spaltennamen
wiedererkannt. Alle Funktionen sind zustandslos und arbeiten auf den Rohbytes der Datei.
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from datetime import datetime

from .importers import ImportError_, ParsedFile, ParsedTx, clean, decode_bytes

DELIMS = {";": ";", ",": ",", "tab": "\t", "|": "|"}
ENCODINGS = ("auto", "utf-8", "cp1252", "latin-1")
DATE_FORMATS = ("auto", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
                "%Y/%m/%d", "%Y%m%d", "%d/%m/%y", "%m/%d/%y")
_AUTO_ORDER = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y%m%d", "%d/%m/%y")
DECIMALS = ("auto", "comma", "dot")
FIELDS = ("date", "value_date", "payee", "booking_text", "iban", "currency", "amount", "debit", "credit", "sign")


class MappingMismatch(ImportError_):
    """Die Datei passt nicht (mehr) zur gespeicherten Zuordnung."""

    def __init__(self, msg: str, missing: list[str] | None = None):
        super().__init__(msg)
        self.missing = missing or []


# ----------------------------------------------------------------------------- Dekodieren / Zerlegen

def _decode(data: bytes, enc: str) -> str:
    if enc in (None, "", "auto"):
        return decode_bytes(data)
    return data.decode(enc, errors="replace")


def sniff_delimiter(text: str) -> str:
    lines = [ln for ln in text.splitlines()[:40] if ln.strip()]
    best, best_score = ";", -1
    for d in (";", "\t", ",", "|"):
        nz = Counter(c for c in (ln.count(d) for ln in lines) if c > 0)
        if not nz:
            continue
        mode, freq = nz.most_common(1)[0]
        score = freq * 100 + min(mode, 50)
        if score > best_score:
            best, best_score = d, score
    return best


def _delim_char(name: str, text: str) -> str:
    return DELIMS.get(name) or sniff_delimiter(text)


def _delim_name(ch: str) -> str:
    return {"\t": "tab"}.get(ch, ch)


def _rows(text: str, delim: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text), delimiter=delim, quotechar='"'))


_NUM = re.compile(r"^[-+(]?\s*\d[\d.,' ]*\s*[)-]?\s*(?:€|eur|usd|chf|gbp)?$", re.I)
_DATE = re.compile(r"^\d{1,4}[./-]\d{1,2}[./-]\d{1,4}(?:[ T].*)?$|^\d{8}$")


def _looks_like_data(c: str) -> bool:
    c = c.strip()
    return bool(_NUM.match(c) or _DATE.match(c))


def guess_header_row(rows: list[list[str]]) -> int | None:
    widths = Counter(len([c for c in r if c.strip()]) for r in rows[:60] if any(c.strip() for c in r))
    if not widths:
        return None
    common = max(widths)                         # breiteste, häufig vorkommende Zeilenbreite
    common = max((w for w, n in widths.items() if n >= 2), default=common)
    for i, r in enumerate(rows[:30]):
        cells = [c.strip() for c in r if c.strip()]
        if len(cells) >= 2 and len(cells) >= 0.6 * common and not any(_looks_like_data(c) for c in cells):
            return i
    return None


# ----------------------------------------------------------------------------- Zahlen / Datum

def parse_number(s: str, decimal: str = "auto") -> int:
    raw = clean(s)
    if not raw:
        raise ValueError("leer")
    neg = False
    t = raw
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    t = re.sub(r"[A-Za-z€$£\s'’]", "", t.replace(" ", ""))
    if t.endswith("-"):
        neg, t = True, t[:-1]
    if t.startswith("-"):
        neg, t = (not neg) or neg, t[1:]
        neg = True
    elif t.startswith("+"):
        t = t[1:]
    if not t or not re.fullmatch(r"[\d.,]+", t):
        raise ValueError(f"Betrag nicht lesbar: {raw}")
    if decimal == "comma":
        t = t.replace(".", "").replace(",", ".")
    elif decimal == "dot":
        t = t.replace(",", "")
    else:
        if "," in t and "." in t:
            if t.rfind(",") > t.rfind("."):
                t = t.replace(".", "").replace(",", ".")
            else:
                t = t.replace(",", "")
        elif "," in t:
            t = t.replace(".", "").replace(",", ".") if re.search(r",\d{1,2}$", t) or t.count(",") == 1 else t.replace(",", "")
        elif "." in t:
            if t.count(".") > 1 or re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
                t = t.replace(".", "")
    try:
        whole, _, frac = t.partition(".")
        cents = int(whole or 0) * 100 + int((frac + "00")[:2])
    except ValueError as e:
        raise ValueError(f"Betrag nicht lesbar: {raw}") from e
    return -cents if neg else cents


def parse_date_cell(s: str, fmt: str = "auto") -> str | None:
    t = clean(s)
    if not t:
        return None
    t = re.split(r"[ T]", t, maxsplit=1)[0] if re.match(r"^\d", t) else t
    for f in ((fmt,) if fmt != "auto" else _AUTO_ORDER):
        try:
            return datetime.strptime(t, f).date().isoformat()
        except ValueError:
            continue
    return None


def guess_date_format(values: list[str]) -> str:
    vals = [v for v in values if clean(v)][:50]
    if not vals:
        return "auto"
    for f in _AUTO_ORDER + ("%m/%d/%Y", "%m/%d/%y"):
        if all(parse_date_cell(v, f) for v in vals):
            return f
    return "auto"


# ----------------------------------------------------------------------------- Zuordnung

_KEYWORDS = {
    "date": ("buchungstag", "buchungsdatum", "buchung", "datum", "booking date", "date", "transaktionsdatum", "bookingdate"),
    "value_date": ("valuta", "wertstellung", "value date", "valutadatum", "wertstellungstag"),
    "payee": ("auftraggeber/empfänger", "empfänger", "zahlungsempfänger", "begünstigter", "beguenstigter", "auftraggeber",
              "name", "payee", "gegenseite", "counterparty", "gegenpartei", "partnername"),
    "purpose": ("verwendungszweck", "zweck", "buchungstext", "beschreibung", "description", "memo", "reference", "text"),
    "booking_text": ("buchungsart", "vorgang", "umsatzart", "transaktionstyp", "type", "art"),
    "iban": ("iban", "kontonummer", "gegenkonto", "iban empfänger", "counterparty iban"),
    "currency": ("währung", "waehrung", "currency"),
    "amount": ("betrag", "umsatz", "amount", "summe", "value"),
    "debit": ("soll", "belastung", "debit", "ausgabe", "ausgaben"),
    "credit": ("haben", "gutschrift", "credit", "einnahme", "einnahmen"),
    "sign": ("soll/haben", "s/h", "debit/credit", "kennzeichen", "vorzeichen"),
}


def suggest_columns(headers: list[str]) -> dict:
    norm = [h.strip().lower() for h in headers]
    used: set[int] = set()
    out: dict = {"purpose": []}

    def pick(role: str) -> str:
        for kw in _KEYWORDS[role]:                       # erst exakte, dann Teiltreffer
            for i, n in enumerate(norm):
                if i not in used and n == kw:
                    used.add(i); return headers[i]
        for kw in _KEYWORDS[role]:
            for i, n in enumerate(norm):
                if i not in used and n and kw in n and len(kw) >= 4:
                    used.add(i); return headers[i]
        return ""

    for role in ("sign", "value_date", "date", "debit", "credit", "amount", "iban", "currency", "payee", "booking_text"):
        out[role] = pick(role)
    p = pick("purpose")
    out["purpose"] = [p] if p else []
    out["amount_mode"] = "split" if (out["debit"] and out["credit"] and not out["amount"]) else "single"
    return out


def default_mapping() -> dict:
    return {"version": 1, "encoding": "auto", "delimiter": "auto", "header_row": 0, "decimal": "auto",
            "date_format": "auto", "invert": False, "amount_mode": "single", "purpose": [],
            **{f: "" for f in FIELDS}}


def normalize_mapping(m: dict | str | None) -> dict:
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError as e:
            raise ImportError_("Spalten-Zuordnung ist kein gültiges JSON.") from e
    if not isinstance(m, dict):
        raise ImportError_("Spalten-Zuordnung fehlt.")
    out = default_mapping()
    s = lambda v: str(v or "")[:200]
    for f in FIELDS:
        out[f] = s(m.get(f))
    out["purpose"] = [s(x) for x in (m.get("purpose") or [])][:4] if isinstance(m.get("purpose"), list) else ([s(m["purpose"])] if m.get("purpose") else [])
    out["purpose"] = [x for x in out["purpose"] if x]
    out["encoding"] = m.get("encoding") if m.get("encoding") in ENCODINGS else "auto"
    out["delimiter"] = m.get("delimiter") if m.get("delimiter") in DELIMS else "auto"
    out["decimal"] = m.get("decimal") if m.get("decimal") in DECIMALS else "auto"
    out["date_format"] = m.get("date_format") if m.get("date_format") in DATE_FORMATS else "auto"
    out["invert"] = bool(m.get("invert"))
    out["amount_mode"] = "split" if m.get("amount_mode") == "split" else "single"
    hr = m.get("header_row", 0)
    out["header_row"] = None if hr in (None, "", "none", -1) else max(0, min(int(hr), 200))
    return out


def _headers_and_body(rows: list[list[str]], header_row: int | None) -> tuple[list[str], list[list[str]]]:
    if header_row is None:
        width = max((len(r) for r in rows[:50]), default=0)
        return [f"Spalte {i + 1}" for i in range(width)], rows
    if header_row >= len(rows):
        raise ImportError_("Kopfzeile liegt hinter dem Dateiende.")
    return [clean(c) for c in rows[header_row]], rows[header_row + 1:]


def _required(m: dict) -> list[str]:
    cols = [m["date"], *m["purpose"]]
    cols += [m["debit"], m["credit"]] if m["amount_mode"] == "split" else [m["amount"]]
    cols += [m[f] for f in ("value_date", "payee", "booking_text", "iban", "currency", "sign")]
    return [c for c in cols if c]


def _validate(m: dict, headers: list[str]) -> None:
    if not m["date"]:
        raise ImportError_("Spalte für das Buchungsdatum ist nicht zugeordnet.")
    if m["amount_mode"] == "single" and not m["amount"]:
        raise ImportError_("Spalte für den Betrag ist nicht zugeordnet.")
    if m["amount_mode"] == "split" and not (m["debit"] or m["credit"]):
        raise ImportError_("Spalte(n) für Soll/Haben sind nicht zugeordnet.")
    missing = [c for c in _required(m) if c not in headers]
    if missing:
        raise MappingMismatch("Diese Datei hat nicht die erwarteten Spalten: " + ", ".join(missing), missing)


# ----------------------------------------------------------------------------- Analyse + Parsen

def analyze(data: bytes, overrides: dict | None = None) -> dict:
    """Erkennt Kodierung, Trennzeichen, Kopfzeile und schlägt eine Zuordnung vor."""
    o = overrides or {}
    enc = o.get("encoding") if o.get("encoding") in ENCODINGS else "auto"
    text = _decode(data, enc)
    delim = _delim_char(o.get("delimiter", "auto"), text)
    rows = _rows(text, delim)
    if "header_row" in o and o["header_row"] not in (None, ""):
        hr = None if o["header_row"] in ("none", -1) else int(o["header_row"])
    elif "header_row" in o:
        hr = None
    else:
        hr = guess_header_row(rows)
    headers, body = _headers_and_body(rows, hr)
    sample = [[clean(c) for c in r] for r in body[:6]]
    sug = suggest_columns(headers) if headers else {}
    if sug.get("date"):
        i = headers.index(sug["date"])
        sug["date_format"] = guess_date_format([r[i] for r in body[:50] if len(r) > i])
    return {"encoding": enc, "delimiter": _delim_name(delim), "header_row": hr, "headers": headers,
            "sample": sample, "row_count": len([r for r in body if any(c.strip() for c in r)]),
            "raw_preview": [[clean(c) for c in r][:8] for r in rows[:8]], "suggested": sug}


def parse_with_mapping(data: bytes, mapping: dict | str) -> tuple[ParsedFile, dict]:
    m = normalize_mapping(mapping)
    text = _decode(data, m["encoding"])
    rows = _rows(text, _delim_char(m["delimiter"], text))
    headers, body = _headers_and_body(rows, m["header_row"])
    _validate(m, headers)
    ix = lambda name: headers.index(name) if name else None
    i_date, i_val, i_payee, i_text = ix(m["date"]), ix(m["value_date"]), ix(m["payee"]), ix(m["booking_text"])
    i_iban, i_cur, i_sign = ix(m["iban"]), ix(m["currency"]), ix(m["sign"])
    i_amt, i_deb, i_cre = ix(m["amount"]), ix(m["debit"]), ix(m["credit"])
    i_purpose = [headers.index(c) for c in m["purpose"]]

    def cell(row: list[str], i: int | None) -> str:
        return row[i] if i is not None and i < len(row) else ""

    pf = ParsedFile("other", None)
    stats = {"rows": 0, "parsed": 0, "skipped": 0, "problems": []}
    for n, row in enumerate(body, start=1):
        if not any(c.strip() for c in row):
            continue
        stats["rows"] += 1
        d = parse_date_cell(cell(row, i_date), m["date_format"])
        if not d:
            stats["skipped"] += 1
            if len(stats["problems"]) < 3:
                stats["problems"].append(f"Zeile {n}: Datum „{clean(cell(row, i_date))}“ nicht lesbar")
            continue
        try:
            if m["amount_mode"] == "split":
                deb, cre = clean(cell(row, i_deb)), clean(cell(row, i_cre))
                cents = (parse_number(cre, m["decimal"]) if cre else 0) - abs(parse_number(deb, m["decimal"]) if deb else 0)
                if not deb and not cre:
                    raise ValueError("kein Betrag")
            else:
                cents = parse_number(cell(row, i_amt), m["decimal"])
                if i_sign is not None:
                    flag = clean(cell(row, i_sign)).lower()
                    if flag[:1] in ("s", "d") or flag in ("dbit", "debit", "soll"):
                        cents = -abs(cents)
                    elif flag[:1] in ("h", "c") or flag in ("crdt", "credit", "haben"):
                        cents = abs(cents)
        except ValueError as e:
            stats["skipped"] += 1
            if len(stats["problems"]) < 3:
                stats["problems"].append(f"Zeile {n}: {e}")
            continue
        if m["invert"]:
            cents = -cents
        iban = re.sub(r"\s+", "", cell(row, i_iban)).upper()
        pf.transactions.append(ParsedTx(
            booking_date=d,
            value_date=parse_date_cell(cell(row, i_val), m["date_format"]) if i_val is not None else None,
            payee=clean(cell(row, i_payee)),
            purpose=clean(" ".join(clean(cell(row, i)) for i in i_purpose)),
            booking_text=clean(cell(row, i_text)),
            amount_cents=cents,
            currency=(clean(cell(row, i_cur)).upper()[:3] or "EUR"),
            counterparty_iban=iban if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban) else None,
        ))
        stats["parsed"] += 1
    return pf, stats


def matches(data: bytes, mapping: dict | str) -> bool:
    """Passt die Datei zur gespeicherten Zuordnung (alle Spalten vorhanden)?"""
    try:
        parse_with_mapping(data[:200_000], mapping)
        return True
    except ImportError_:
        return False
