"""Kategorie-Vorlage mit Unterkategorien (eine Ebene).

`apply_template()` ist idempotent: Vorhandene Standardkategorien werden umbenannt und einer
Oberkategorie zugeordnet (Umsätze und Regeln bleiben erhalten), fehlende Ober-/Unterkategorien
werden angelegt, passende Standardregeln wandern auf die jeweilige Unterkategorie.
"""
from __future__ import annotations

import sqlite3

from .db import now_iso

# Oberkategorie -> (Art, Farbe, [Unterkategorien])
TREE: dict[str, tuple[str, str, list[str]]] = {
    "Einnahmen": ("income", "#2e9e6b", [
        "Lohn / Gehalt", "Bonuszahlungen / Tantieme", "Staatliche Leistungen & Zuschüsse",
        "Einnahmen aus Verkäufen", "Steuerrückerstattungen", "Zinsen / Dividenden",
        "Erstattungen", "Sonstige Einnahmen"]),
    "Wohnen": ("expense", "#4f7cff", [
        "Miete", "Strom / Wasser / Gas / Heizkosten", "Haus- / Baufinanzierung",
        "Haushaltswaren / Haushaltsgeräte", "Möbel / Wohnaccessoires",
        "Renovierung / Instandhaltung", "Garten"]),
    "Lebenshaltung": ("expense", "#f2a33a", [
        "Lebensmittel / Getränke", "Hygieneartikel / Reinigungsmittel", "Kleidung / Schuhe",
        "Frisör / Wellness / Schönheitspflege", "Elektronik / Computer", "Bücher / Musik / DVDs",
        "Geschenke / Spenden", "Shopping"]),
    "Auto und öffentliche Verkehrsmittel": ("expense", "#8e6bd8", [
        "Tanken", "Öffentliche Verkehrsmittel", "Parken / Garage", "Werkstatt / Reparaturen",
        "Kraftfahrzeugversicherung", "Kraftfahrzeugsteuern", "Kraft- und Fahrräder", "Autokauf"]),
    "Kommunikation": ("expense", "#3aa6c9", [
        "Telefon / Internet / Fernsehen", "Abonnements", "Post"]),
    "Versicherungen": ("expense", "#5a6b8c", [
        "Haftpflichtversicherung", "Hausratversicherung", "Berufsunfähigkeitsversicherung",
        "Lebens- / Rentenversicherung", "Zusatzversicherungen"]),
    "Freizeit": ("expense", "#c9a227", [
        "Restaurants / Cafes / Bars", "Kino / Kunst / Kultur", "Sport / Fitness"]),
    "Reise": ("expense", "#2f8fd1", [
        "Hotel / Unterkunft", "Reisekosten (Flug / Bahn / Schiff)", "Mietwagen", "Sonstige Ausgaben Reise"]),
    "Bildung und Beruf": ("expense", "#7f8c4a", [
        "Weiterbildung", "Dienstreise / Spesen", "Sonstige Ausgaben Bildung und Beruf"]),
    "Gesundheit": ("expense", "#47b39b", [
        "Apotheke / Medikamente", "Arztbehandlung", "Brille / Kontaktlinsen", "Sonstige Ausgaben Gesundheit"]),
    "Kinder": ("expense", "#d8738f", [
        "Kinderbetreuung", "Schulgeld", "Schulausflug", "Kinderbekleidung", "Spielwaren", "Taschengeld",
        "Freizeitaktivitäten Kinder", "Pflegeprodukte Kinder", "Unterhaltszahlungen", "Sonstige Ausgaben Kinder"]),
    "Bank": ("expense", "#a65e3a", ["Bankentgelte"]),
    "Sparen und Anlegen": ("transfer", "#3f8f5f", ["Geldanlage", "Altersvorsorge", "Bausparen"]),
    "Unkategorisierte Ausgaben": ("expense", "#9aa0a6", [
        "Abgaben / Gebühren", "Bargeldauszahlung"]),
}

# alter Standardname -> (neuer Name, Oberkategorie oder None = bleibt oberste Ebene)
RENAMES: dict[str, tuple[str, str | None]] = {
    "Gehalt": ("Lohn / Gehalt", "Einnahmen"),
    "Sonstige Einnahmen": ("Sonstige Einnahmen", "Einnahmen"),
    "Erstattungen": ("Erstattungen", "Einnahmen"),
    "Nebenkosten & Energie": ("Strom / Wasser / Gas / Heizkosten", "Wohnen"),
    "Lebensmittel": ("Lebensmittel / Getränke", "Lebenshaltung"),
    "Shopping": ("Shopping", "Lebenshaltung"),
    "Restaurants & Bars": ("Restaurants / Cafes / Bars", "Freizeit"),
    "Mobilität": ("Auto und öffentliche Verkehrsmittel", None),
    "Telefon & Internet": ("Telefon / Internet / Fernsehen", "Kommunikation"),
    "Abos & Streaming": ("Abonnements", "Kommunikation"),
    "Freizeit & Unterhaltung": ("Freizeit", None),
    "Reisen": ("Reise", None),
    "Bargeld": ("Bargeldauszahlung", "Unkategorisierte Ausgaben"),
    "Gebühren & Steuern": ("Abgaben / Gebühren", "Unkategorisierte Ausgaben"),
    "Sonstiges": ("Unkategorisierte Ausgaben", None),
    "Sparen & Investieren": ("Sparen und Anlegen", None),
    "Bildung": ("Bildung und Beruf", None),
}

# Standardregeln (Muster) auf Unterkategorien verteilen
MOVE_RULES: dict[str, list[str]] = {
    "Miete": ["miete", "kaltmiete", "warmmiete", "vermieter", "hausverwaltung", "hausgeld", "wohnungsbau"],
    "Tanken": ["shell", "re:\\baral\\b", "esso", "jet tankstelle", "totalenergies", "tankstelle"],
    "Öffentliche Verkehrsmittel": ["db vertrieb", "deutsche bahn", "bahn.de", "re:\\brmv\\b", "re:\\bhvv\\b",
                                   "re:\\bbvg\\b", "re:\\bmvv\\b", "flixbus", "deutschlandticket"],
    "Parken / Garage": ["parkhaus", "parkgebühr"],
    "Haftpflichtversicherung": ["haftpflicht"],
    "Zinsen / Dividenden": ["zinsgutschrift", "dividende"],
    "Staatliche Leistungen & Zuschüsse": ["kindergeld", "familienkasse", "re:\\bbafög"],
    "Hotel / Unterkunft": ["hotel", "airbnb", "hostel", "booking.com"],
    "Reisekosten (Flug / Bahn / Schiff)": ["lufthansa", "ryanair", "easyjet", "eurowings"],
    "Kino / Kunst / Kultur": ["kino", "cinemaxx", "cinestar", "theater", "museum", "ticketmaster", "eventim"],
    "Sport / Fitness": ["fitx", "mcfit", "fitness", "urban sports", "schwimmbad"],
    "Geldanlage": ["trade republic", "scalable", "wertpapier", "re:\\betf\\b", "depot", "consorsbank",
                   "re:\\bcoinbase\\b", "bitpanda", "flatex"],
    "Bausparen": ["bausparen"],
    "Bankentgelte": ["kontoführung", "kontofuehrung", "kontogebühr", "sollzinsen", "dispozinsen", "jahresgebühr"],
    "Elektronik / Computer": ["mediamarkt", "media markt", "saturn", "cyberport", "apple store"],
    "Hygieneartikel / Reinigungsmittel": ["re:\\bdm\\b", "dm-drogerie", "rossmann"],
    "Bücher / Musik / DVDs": ["thalia"],
    "Möbel / Wohnaccessoires": ["ikea"],
    "Weiterbildung": ["udemy", "coursera", "volkshochschule", "re:\\bvhs\\b"],
    "Schulgeld": ["schulgeld"],
    "Kinderbetreuung": ["kita"],
    "Apotheke / Medikamente": ["apotheke", "docmorris"],
    "Arztbehandlung": ["arzt", "zahnarzt", "praxis", "physiotherap", "klinik", "krankenhaus", "labor"],
    "Brille / Kontaktlinsen": ["optiker", "fielmann"],
    "Renovierung / Instandhaltung": ["obi ", "bauhaus", "hornbach"],
}

# zusätzliche Standardregeln: Unterkategorie -> (Richtung, [Muster])
NEW_RULES: dict[str, tuple[str, list[str]]] = {
    "Kraftfahrzeugsteuern": ("out", ["kfz-steuer", "kraftfahrzeugsteuer", "hauptzollamt"]),
    "Hausratversicherung": ("out", ["hausrat"]),
    "Lebens- / Rentenversicherung": ("out", ["lebensversicherung", "rentenversicherung"]),
    "Berufsunfähigkeitsversicherung": ("out", ["berufsunfähigkeit"]),
    "Post": ("out", ["deutsche post", "re:\\bdhl\\b"]),
    "Spielwaren": ("out", ["spielwaren", "smyths", "mytoys", "re:\\blego\\b"]),
    "Schulausflug": ("out", ["klassenfahrt", "schulausflug"]),
    "Unterhaltszahlungen": ("out", ["re:\\bunterhalt\\b"]),
    "Frisör / Wellness / Schönheitspflege": ("out", ["friseur", "frisör", "barber"]),
}

TOP_NAMES = tuple(TREE)


def _cat(conn: sqlite3.Connection, name: str):
    return conn.execute("SELECT * FROM categories WHERE name=?", (name,)).fetchone()


def _ensure(conn: sqlite3.Connection, name: str, kind: str, color: str, parent_id: int | None) -> int:
    row = _cat(conn, name)
    if row:
        conn.execute("UPDATE categories SET parent_id=? WHERE id=? AND parent_id IS NOT ?",
                     (parent_id, row["id"], parent_id))
        return row["id"]
    return conn.execute("INSERT INTO categories(name,kind,color,parent_id) VALUES(?,?,?,?)",
                        (name, kind, color, parent_id)).lastrowid


def apply_template(conn: sqlite3.Connection, only: set[str] | None = None) -> dict:
    """Wendet die Vorlage an (oder nur die Oberkategorien in `only`). Rückgabe: Anzahl umbenannt / neu angelegt."""
    renamed = created = 0
    # 1. Standardkategorien umbenennen (nur, wenn der neue Name noch frei ist)
    for old, (new, _parent) in RENAMES.items():
        if old == new or (only is not None and new not in only and _parent not in only):
            continue
        if _cat(conn, old) and not _cat(conn, new):
            conn.execute("UPDATE categories SET name=? WHERE name=?", (new, old))
            renamed += 1
    # 2. Oberkategorien sicherstellen
    top_ids: dict[str, int] = {}
    tree = {k: v for k, v in TREE.items() if only is None or k in only}
    for top, (kind, color, _subs) in tree.items():
        existed = _cat(conn, top) is not None
        top_ids[top] = _ensure(conn, top, kind, color, None)
        created += 0 if existed else 1
    # 3. Unterkategorien sicherstellen / zuordnen
    new_subs: list[str] = []
    for top, (kind, color, subs) in tree.items():
        for s in subs:
            existed = _cat(conn, s) is not None
            _ensure(conn, s, kind, color, top_ids[top])
            if not existed:
                created += 1
                new_subs.append(s)
    # 4. Regeln: Standardregeln auf Unterkategorien verteilen, neue ergänzen
    in_scope = {s for _k, _c, subs in tree.values() for s in subs}
    for sub, patterns in MOVE_RULES.items():
        row = _cat(conn, sub)
        if not row or sub not in in_scope:
            continue
        for p in patterns:
            conn.execute("UPDATE rules SET category_id=? WHERE source='default' AND pattern=?", (row["id"], p))
    ts = now_iso()
    for sub in new_subs:
        if sub in NEW_RULES:
            direction, patterns = NEW_RULES[sub]
            cid = _cat(conn, sub)["id"]
            for p in patterns:
                if not conn.execute("SELECT 1 FROM rules WHERE source='default' AND pattern=?", (p,)).fetchone():
                    conn.execute("INSERT INTO rules(category_id,pattern,direction,priority,source,created_at) "
                                 "VALUES(?,?,?,0,'default',?)", (cid, p, direction, ts))
    return {"renamed": renamed, "created": created}


def descendants(conn: sqlite3.Connection, category_id: int) -> list[int]:
    """Die Kategorie selbst plus ihre Unterkategorien."""
    ids = [category_id]
    ids += [r["id"] for r in conn.execute("SELECT id FROM categories WHERE parent_id=?", (category_id,))]
    return ids


RETIRED = "Sonstige Unkategorisierte Ausgaben"      # ab 0.6.2 entfallen, Umsätze liegen direkt auf der Oberkategorie


def retire_category(conn: sqlite3.Connection) -> bool:
    """Einmalige Migration: Umsätze/Regeln der entfallenen Unterkategorie auf die Oberkategorie verschieben."""
    row = _cat(conn, RETIRED)
    if not row:
        return False
    parent = _cat(conn, "Unkategorisierte Ausgaben") or None
    pid = parent["id"] if parent else _ensure(conn, "Unkategorisierte Ausgaben", "expense", "#9aa0a6", None)
    if pid == row["id"]:
        return False
    conn.execute("UPDATE transactions SET category_id=? WHERE category_id=?", (pid, row["id"]))
    conn.execute("UPDATE rules SET category_id=? WHERE category_id=?", (pid, row["id"]))
    conn.execute("DELETE FROM budgets WHERE category_id=?", (row["id"],))
    conn.execute("DELETE FROM categories WHERE id=?", (row["id"],))
    return True


# Spätere Ergänzungen der Vorlage: werden beim Start einmalig auch in bestehende Datenbanken eingetragen,
# in denen die Vorlage schon angewendet wurde (Ober-/Unterkategorien, die du gelöscht hast, kommen nicht zurück).
TREE_V2_TOPS = {"Bildung und Beruf", "Gesundheit", "Kinder"}


def migrate_template(conn: sqlite3.Connection) -> None:
    from .db import get_setting, set_setting
    if get_setting(conn, "tree_version", "1") >= "2":
        return
    if _cat(conn, "Lebenshaltung"):                       # Vorlage wurde bereits angewendet
        apply_template(conn, only=TREE_V2_TOPS)
    set_setting(conn, "tree_version", "2")
