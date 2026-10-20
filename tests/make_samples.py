"""Erzeugt realistisch aufgebaute (aber frei erfundene) Beispiel-CSVs im ING- und comdirect-Format.
Aufruf: python -m tests.make_samples  -> schreibt tests/samples/*.csv"""
import os
import random
import zlib
from datetime import date

OUT = os.path.join(os.path.dirname(__file__), "samples")
IBAN_ING = "DE12 5001 0517 0123 4567 89"
OWN_NAME = "Max Mustermann"


def de(cents: int) -> str:
    s = f"{abs(cents) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if cents < 0 else "") + s


def months():
    y, m = 2026, 4
    while (y, m) <= (2026, 10):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def build():
    rnd = random.Random(42)
    ing, cd = [], []   # (date, payee, purpose, text, cents)
    today = date(2026, 10, 18)

    def add(lst, y, m, d, payee, purpose, text, cents):
        dt = date(y, m, d)
        if dt <= today:
            lst.append((dt, payee, purpose, text, cents))

    for y, m in months():
        add(ing, y, m, 28, "Musterfirma GmbH", f"Gehalt {m:02d}/{y}", "Gutschrift", 285000)
        add(ing, y, m, 1, "Hausverwaltung Müller", "Miete Wohnung 2.OG", "Dauerauftrag", -95000)
        add(ing, y, m, 5, "Stadtwerke Musterstadt", "Abschlag Strom", "Lastschrift", -6200)
        add(ing, y, m, 3, "Telekom Deutschland GmbH", "Rechnung Mobilfunk", "Lastschrift", -3995)
        add(ing, y, m, 12, "Netflix International B.V.", "Netflix", "Lastschrift", -1299)
        add(ing, y, m, 8, "Spotify AB", "Spotify Premium", "Lastschrift", -1099)
        add(ing, y, m, 2, "Trade Republic Bank GmbH", "Sparplan ETF", "Dauerauftrag", -20000)
        add(ing, y, m, 10, OWN_NAME, "Umbuchung comdirect", "Überweisung", -30000)
        add(ing, y, m, 6, "Rundfunk ARD, ZDF, DRadio", "Rundfunkbeitrag", "Lastschrift", -1836)
        if m in (1, 4, 7, 10):
            add(ing, y, m, 15, "HUK-COBURG Versicherung", "Kfz-Haftpflicht Quartal", "Lastschrift", -14820)
        for _ in range(rnd.randint(4, 6)):
            add(ing, y, m, rnd.randint(1, 27), rnd.choice(["REWE Markt GmbH", "LIDL Dienstleistung", "EDEKA Center"]),
                "Kartenzahlung Einkauf", "Lastschrift", -rnd.randint(1800, 7500))
        for _ in range(2):
            add(ing, y, m, rnd.randint(1, 27), "ARAL Tankstelle", "Tanken", "Lastschrift", -rnd.randint(4500, 7500))
        for _ in range(2):
            add(ing, y, m, rnd.randint(1, 27), rnd.choice(["Pizzeria Roma", "Restaurant Zum Löwen", "Starbucks"]),
                "Kartenzahlung", "Lastschrift", -rnd.randint(1200, 4800))
        add(ing, y, m, rnd.randint(10, 20), "AMAZON PAYMENTS EUROPE", "Bestellung", "Lastschrift", -rnd.randint(1500, 9000))
        add(ing, y, m, 14, "Geldautomat", "Bargeldauszahlung", "Bargeldauszahlung", -5000)
        # comdirect
        add(cd, y, m, 10, OWN_NAME, "Umbuchung ING", "Überweisung", 30000)
        add(cd, y, m, 4, "McFit GmbH", "Mitgliedsbeitrag", "Lastschrift / Belastung", -2490)
        for _ in range(3):
            add(cd, y, m, rnd.randint(1, 27), rnd.choice(["EDEKA Center", "ALDI SÜD", "Alnatura"]),
                "Einkauf", "Lastschrift / Belastung", -rnd.randint(1500, 6500))
        add(cd, y, m, rnd.randint(5, 25), "Apotheke am Markt", "Rezept", "Lastschrift / Belastung", -rnd.randint(800, 3200))
        add(cd, y, m, rnd.randint(5, 25), "ZALANDO SE", "Bestellung", "Lastschrift / Belastung", -rnd.randint(3000, 9000))
    add(cd, 2026, 8, 20, "Booking.com B.V.", "Hotel Ostsee", "Lastschrift / Belastung", -45000)
    add(cd, 2026, 7, 5, "Finanzamt Musterstadt", "Steuererstattung 2025", "Gutschrift", 31250)
    # zwei identische Umsätze am selben Tag (müssen beide erhalten bleiben)
    add(cd, 2026, 9, 21, "Kino Cinemaxx", "2x Ticket", "Lastschrift / Belastung", -1450)
    add(cd, 2026, 9, 21, "Kino Cinemaxx", "2x Ticket", "Lastschrift / Belastung", -1450)
    ing.sort(key=lambda x: x[0], reverse=True)
    cd.sort(key=lambda x: x[0], reverse=True)
    return ing, cd


def write():
    os.makedirs(OUT, exist_ok=True)
    ing, cd = build()
    balance = 523456
    lines = [
        "Umsatzanzeige;Datei erstellt am: 18.10.2026 22:10", ";Letztes Update: aktuell", "",
        f"IBAN;{IBAN_ING}", "Kontoname;Girokonto", "Bank;ING", f"Kunde;{OWN_NAME}",
        "Zeitraum;01.04.2026 - 18.10.2026", f"Saldo;{de(balance)};EUR", "",
        "Sortierung;Datum absteigend", "In der CSV-Datei finden Sie alle bereits gebuchten Umsätze.", "",
        "Buchung;Valuta;Auftraggeber/Empfänger;Buchungstext;Verwendungszweck;Saldo;Währung;Betrag;Währung",
    ]
    for dt, payee, purpose, text, cents in ing:
        d = dt.strftime("%d.%m.%Y")
        lines.append(f"{d};{d};{payee};{text};{purpose};{de(balance)};EUR;{de(cents)};EUR")
        balance -= cents
    with open(os.path.join(OUT, "ing_beispiel.csv"), "w", encoding="cp1252", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")

    lines = [
        '"Umsatzübersicht Girokonto";"Zeitraum: 01.04.2026 - 18.10.2026";',
        '"Neuer Kontostand";"1.234,56 EUR";', "",
        '"Buchungstag";"Wertstellung (Valuta)";"Vorgang";"Buchungstext";"Umsatz in EUR";',
        '"offen";"";"Lastschrift / Belastung";"Auftraggeber: NOCH VORGEMERKT Buchungstext: bitte ignorieren";"-9,99";',
    ]
    for i, (dt, payee, purpose, text, cents) in enumerate(cd):
        d = dt.strftime("%d.%m.%Y")
        bt = f"Auftraggeber: {payee} Buchungstext: {purpose} Ref. {zlib.crc32(payee.encode()) % 99999}/{i}"
        lines.append(f'"{d}";"{d}";"{text}";"{bt}";"{de(cents)}";')
    with open(os.path.join(OUT, "comdirect_beispiel.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return ing, cd


if __name__ == "__main__":
    ing, cd = write()
    print(f"ING: {len(ing)} Umsätze, comdirect: {len(cd)} Umsätze -> {OUT}")
