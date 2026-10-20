"""Regelbasierte Umsatzkategorisierung (komplett lokal, keine Cloud).

Reihenfolge je Umsatz:
 1. Gegenkonto-IBAN gehört zu einem eigenen Konto      -> Umbuchung
 2. eigene Regeln (source='user')
 3. eigene Namen (Einstellung "own_names") als Empfänger -> Umbuchung
 4. Standardregeln (source='default')
 5. Fallback: Sonstige Einnahmen bzw. Sonstiges
Manuell gesetzte Kategorien (category_source='manual') werden nie überschrieben.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from .db import get_setting, now_iso

DEFAULT_CATEGORIES = [
    ("Gehalt", "income", "#2e9e6b"),
    ("Sonstige Einnahmen", "income", "#5bbf8f"),
    ("Erstattungen", "income", "#8fd3b0"),
    ("Wohnen", "expense", "#4f7cff"),
    ("Nebenkosten & Energie", "expense", "#6f93ff"),
    ("Lebensmittel", "expense", "#f2a33a"),
    ("Restaurants & Bars", "expense", "#e8743b"),
    ("Mobilität", "expense", "#8e6bd8"),
    ("Telefon & Internet", "expense", "#3aa6c9"),
    ("Abos & Streaming", "expense", "#d45fa8"),
    ("Shopping", "expense", "#d9534f"),
    ("Versicherungen", "expense", "#5a6b8c"),
    ("Gesundheit", "expense", "#47b39b"),
    ("Freizeit & Unterhaltung", "expense", "#c9a227"),
    ("Reisen", "expense", "#2f8fd1"),
    ("Bildung", "expense", "#7f8c4a"),
    ("Bargeld", "expense", "#8a8f98"),
    ("Gebühren & Steuern", "expense", "#a65e3a"),
    ("Sonstiges", "expense", "#9aa0a6"),
    ("Sparen & Investieren", "transfer", "#3f8f5f"),
    ("Umbuchung", "transfer", "#b0b7c3"),
]

# (Kategorie, Richtung, [Muster]) – Muster = Teilstring am Wortanfang oder "re:<regex>"
DEFAULT_RULES = [
    ("Gehalt", "in", ["gehalt", "lohn", "bezüge", "besoldung", "vergütung", "arbeitgeber"]),
    ("Erstattungen", "in", ["erstattung", "rückzahlung", "retoure", "re:\\bstorno"]),
    ("Sonstige Einnahmen", "in", ["kindergeld", "familienkasse", "zinsgutschrift", "dividende", "re:\\bbafög"]),
    ("Wohnen", "out", ["miete", "kaltmiete", "warmmiete", "hausverwaltung", "wohnungsbau", "vermieter", "hausgeld"]),
    ("Nebenkosten & Energie", "out", [
        "stadtwerke", "vattenfall", "re:\\be\\.?on\\b", "enbw", "eprimo", "naturstrom", "ewe ", "re:\\bstrom\\b",
        "re:\\bgas\\b", "wasserwerk", "abfall", "rundfunk", "ard zdf deutschlandradio", "nebenkosten", "tibber",
    ]),
    ("Lebensmittel", "out", [
        "rewe", "edeka", "aldi", "lidl", "penny", "netto marken", "re:\\bnetto\\b", "kaufland", "norma ",
        "tegut", "denn's", "alnatura", "globus", "bäckerei", "baeckerei", "backwerk", "metzgerei",
        "fleischerei", "wochenmarkt", "flink", "gorillas", "picnic",
    ]),
    ("Restaurants & Bars", "out", [
        "restaurant", "mcdonald", "burger king", "kfc", "re:\\bsubway\\b", "starbucks", "lieferando", "wolt",
        "uber eats", "pizzeria", "domino", "vapiano", "café", "cafe ", "imbiss", "kebab", "bar & ",
    ]),
    ("Mobilität", "out", [
        "db vertrieb", "deutsche bahn", "bahn.de", "re:\\brmv\\b", "re:\\bhvv\\b", "re:\\bbvg\\b", "re:\\bmvv\\b",
        "flixbus", "re:\\buber\\b", "re:\\bbolt\\b", "shell", "re:\\baral\\b", "esso", "jet tankstelle",
        "totalenergies", "tankstelle", "re:\\badac\\b", "parkhaus", "parkgebühr", "deutschlandticket",
        "kfz", "sixt", "share now", "tier ", "lime ",
    ]),
    ("Telefon & Internet", "out", [
        "telekom", "vodafone", "telefonica", "re:\\bo2\\b", "1&1", "1und1", "congstar", "unitymedia",
        "sipgate", "freenet", "netzclub", "drillisch", "re:\\bpyur\\b", "mobilcom",
    ]),
    ("Abos & Streaming", "out", [
        "netflix", "spotify", "disney", "amazon prime", "prime video", "dazn", "youtube", "apple.com/bill",
        "itunes", "google one", "audible", "deezer", "paramount", "sky deutschland", "rtl+", "joyn", "icloud",
        "dropbox", "openai", "anthropic", "chatgpt", "github", "adobe", "microsoft 365", "nordvpn", "patreon",
    ]),
    ("Shopping", "out", [
        "amazon", "amzn", "zalando", "ebay", "re:\\botto\\b", "h&m", "re:\\bzara\\b", "ikea", "mediamarkt",
        "media markt", "saturn", "re:\\bdm\\b", "dm-drogerie", "rossmann", "müller", "decathlon", "obi ",
        "bauhaus", "hornbach", "apple store", "re:\\bpaypal\\b", "tchibo", "thalia", "douglas", "cyberport",
    ]),
    ("Versicherungen", "out", [
        "versicherung", "allianz", "re:\\bhuk\\b", "devk", "re:\\baxa\\b", "re:\\bergo\\b", "re:\\bhdi\\b",
        "generali", "cosmos direkt", "haftpflicht", "krankenkasse", "techniker krankenkasse", "re:\\baok\\b",
        "barmer", "re:\\bdak\\b", "re:\\bikk\\b", "krankenversicherung", "rechtsschutz",
    ]),
    ("Gesundheit", "out", [
        "apotheke", "arzt", "zahnarzt", "praxis", "docmorris", "optiker", "fielmann", "physiotherap",
        "klinik", "krankenhaus", "labor",
    ]),
    ("Freizeit & Unterhaltung", "out", [
        "kino", "cinemaxx", "cinestar", "ticketmaster", "eventim", "fitx", "mcfit", "fitness", "urban sports",
        "steam", "playstation", "nintendo", "xbox", "theater", "museum", "schwimmbad", "therme", "sauna",
        "freizeitpark", "zoo ",
    ]),
    ("Reisen", "out", [
        "booking.com", "airbnb", "hotel", "lufthansa", "ryanair", "easyjet", "eurowings", "expedia", "re:\\btui\\b",
        "ferien", "hostel", "check24 reise", "opodo",
    ]),
    ("Bildung", "out", [
        "udemy", "coursera", "volkshochschule", "re:\\bvhs\\b", "studierendenwerk", "semesterbeitrag",
        "studentenwerk", "uni ", "universität", "schulgeld", "kita",
    ]),
    ("Bargeld", "out", ["geldautomat", "bargeld", "re:\\batm\\b", "cash group"]),
    ("Gebühren & Steuern", "out", [
        "kontoführung", "kontofuehrung", "kontogebühr", "finanzamt", "steuer", "sollzinsen", "dispozinsen",
        "jahresgebühr", "bußgeld", "gebühr",
    ]),
    ("Sparen & Investieren", "any", [
        "trade republic", "scalable", "sparplan", "wertpapier", "tagesgeld", "festgeld", "bausparen",
        "re:\\betf\\b", "depot", "consorsbank", "re:\\bcoinbase\\b", "bitpanda", "flatex",
    ]),
]


def seed_defaults(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO categories(name,kind,color) VALUES(?,?,?)", DEFAULT_CATEGORIES
        )
    if conn.execute("SELECT COUNT(*) FROM rules WHERE source='default'").fetchone()[0] == 0:
        ids = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM categories")}
        ts = now_iso()
        for cat, direction, patterns in DEFAULT_RULES:
            if cat not in ids:
                continue
            for p in patterns:
                conn.execute(
                    "INSERT INTO rules(category_id,pattern,direction,priority,source,created_at) "
                    "VALUES(?,?,?,?,'default',?)",
                    (ids[cat], p, direction, 0, ts),
                )


def _normalize_iban(iban: str | None) -> str:
    return re.sub(r"\s+", "", iban or "").upper()


@dataclass
class _Rule:
    category_id: int
    regex: re.Pattern
    direction: str
    source: str
    priority: int
    length: int


def _compile(pattern: str) -> re.Pattern:
    raw = pattern.lower().lstrip()
    if raw.startswith("re:"):
        return re.compile(raw[3:].strip(), re.IGNORECASE)
    # Teilstring nur am Wortanfang (verhindert z. B. "o2" in "foo2");
    # ein Leerzeichen am Ende des Musters erzwingt zusätzlich ein Wortende ("tier " ≠ "tierarzt").
    word_end = raw.endswith(" ")
    end = r"(?![0-9a-zäöüß])" if word_end else ""
    return re.compile(r"(?<![0-9a-zäöüß])" + re.escape(raw.strip()) + end)


class Categorizer:
    def __init__(self, conn: sqlite3.Connection):
        cats = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM categories")}
        self.cat_umbuchung = cats.get("Umbuchung")
        self.cat_other_in = cats.get("Sonstige Einnahmen")
        self.cat_other_out = cats.get("Sonstiges")
        self.own_ibans = {
            _normalize_iban(r["iban"]) for r in conn.execute("SELECT iban FROM accounts") if r["iban"]
        }
        self.own_names = [
            n.strip().lower() for n in get_setting(conn, "own_names", "").split(",") if n.strip()
        ]
        self.rules: list[_Rule] = []
        for r in conn.execute("SELECT category_id,pattern,direction,priority,source FROM rules"):
            try:
                self.rules.append(
                    _Rule(r["category_id"], _compile(r["pattern"]), r["direction"], r["source"],
                          r["priority"], len(r["pattern"]))
                )
            except re.error:
                continue  # kaputte Regex ignorieren
        self.rules.sort(key=lambda x: (-x.priority, -x.length))

    def _match(self, source: str, hay: str, amount_cents: int) -> int | None:
        for r in self.rules:
            if r.source != source:
                continue
            if r.direction == "in" and amount_cents <= 0:
                continue
            if r.direction == "out" and amount_cents >= 0:
                continue
            if r.regex.search(hay):
                return r.category_id
        return None

    def categorize(self, payee: str, purpose: str, booking_text: str,
                   amount_cents: int, counterparty_iban: str | None = None) -> int | None:
        if counterparty_iban and _normalize_iban(counterparty_iban) in self.own_ibans:
            return self.cat_umbuchung
        hay = f"{payee} {purpose} {booking_text}".lower()
        hit = self._match("user", hay, amount_cents)
        if hit:
            return hit
        if self.own_names and any(n in payee.lower() for n in self.own_names):
            return self.cat_umbuchung
        hit = self._match("default", hay, amount_cents)
        if hit:
            return hit
        return self.cat_other_in if amount_cents > 0 else self.cat_other_out


def apply_rules(conn: sqlite3.Connection, only_uncategorized: bool = False) -> int:
    """Kategorisiert alle nicht manuell gesetzten Umsätze neu. Rückgabe: Anzahl Änderungen."""
    cz = Categorizer(conn)
    q = ("SELECT id,payee,purpose,booking_text,amount_cents,counterparty_iban,category_id "
         "FROM transactions WHERE category_source != 'manual'")
    if only_uncategorized:
        q += " AND category_id IS NULL"
    changed = 0
    for t in conn.execute(q).fetchall():
        new = cz.categorize(t["payee"], t["purpose"], t["booking_text"],
                            t["amount_cents"], t["counterparty_iban"])
        if new != t["category_id"]:
            conn.execute("UPDATE transactions SET category_id=? WHERE id=?", (new, t["id"]))
            changed += 1
    return changed
