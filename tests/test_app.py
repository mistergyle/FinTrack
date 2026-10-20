import os
import sys
import tempfile
import unittest
from datetime import date

_tmp = tempfile.mkdtemp()
os.environ["FINANZ_DB"] = os.path.join(_tmp, "t.db")
os.environ["APP_PASSWORD"] = "geheim"
os.environ["SECRET_KEY"] = "test-secret"

from starlette.testclient import TestClient  # noqa: E402

from app import analysis, fints_sync  # noqa: E402
from app.categorizer import Categorizer, apply_rules  # noqa: E402
from app.db import init_db, session, set_setting  # noqa: E402
from app.importers import parse_csv, store_transactions  # noqa: E402
from app.main import create_app  # noqa: E402
from tests import make_samples  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(__file__), "samples")
ING = open(os.path.join(SAMPLES, "ing_beispiel.csv"), "rb").read()
CD = open(os.path.join(SAMPLES, "comdirect_beispiel.csv"), "rb").read()
ING_ROWS, CD_ROWS = make_samples.build()
TODAY = date(2026, 10, 18)


def fresh_db():
    if os.path.exists(os.environ["FINANZ_DB"]):
        os.remove(os.environ["FINANZ_DB"])
    init_db()


def make_accounts(conn):
    a = conn.execute("INSERT INTO accounts(name,bank,iban,created_at) VALUES('ING Giro','ing','DE12500105170123456789','x')").lastrowid
    b = conn.execute("INSERT INTO accounts(name,bank,created_at) VALUES('comdirect Giro','comdirect','x')").lastrowid
    return a, b


class ParserTests(unittest.TestCase):
    def test_ing(self):
        pf = parse_csv(ING)
        self.assertEqual(pf.bank, "ing")
        self.assertEqual(pf.iban, "DE12 5001 0517 0123 4567 89")
        self.assertEqual(len(pf.transactions), len(ING_ROWS))
        first = pf.transactions[0]
        self.assertEqual(first.booking_date, "2026-10-18")
        self.assertLess(first.amount_cents, 0)
        self.assertEqual(sum(t.amount_cents for t in pf.transactions), sum(r[4] for r in ING_ROWS))

    def test_comdirect(self):
        pf = parse_csv(CD)
        self.assertEqual(pf.bank, "comdirect")
        self.assertEqual(len(pf.transactions), len(CD_ROWS))          # "offen" wurde übersprungen
        t = next(t for t in pf.transactions if t.payee == "ZALANDO SE")
        self.assertEqual(t.purpose, "Bestellung")
        self.assertEqual(t.booking_text, "Lastschrift / Belastung")
        self.assertEqual(sum(t.amount_cents for t in pf.transactions), sum(r[4] for r in CD_ROWS))

    def test_unknown(self):
        with self.assertRaises(Exception):
            parse_csv(b"a;b;c\n1;2;3\n")


class StoreAndCategorizeTests(unittest.TestCase):
    def setUp(self):
        fresh_db()

    def test_idempotent_and_same_day_duplicates(self):
        with session() as conn:
            a, b = make_accounts(conn)
            r1 = store_transactions(conn, b, parse_csv(CD).transactions)
            r2 = store_transactions(conn, b, parse_csv(CD).transactions)
            self.assertEqual(r1["added"], len(CD_ROWS))
            self.assertEqual(r2["added"], 0)
            kino = conn.execute("SELECT COUNT(*) FROM transactions WHERE payee LIKE 'Kino%' AND booking_date='2026-09-21'").fetchone()[0]
            self.assertEqual(kino, 2)   # zwei identische Umsätze am selben Tag bleiben beide erhalten

    def test_categories(self):
        with session() as conn:
            set_setting(conn, "own_names", "Max Mustermann")
            a, b = make_accounts(conn)
            store_transactions(conn, a, parse_csv(ING).transactions)
            store_transactions(conn, b, parse_csv(CD).transactions)

            def cat(payee_like):
                r = conn.execute("SELECT c.name FROM transactions t JOIN categories c ON c.id=t.category_id "
                                 "WHERE t.payee LIKE ? LIMIT 1", (payee_like,)).fetchone()
                return r[0]
            self.assertEqual(cat("REWE%"), "Lebensmittel")
            self.assertEqual(cat("Netflix%"), "Abos & Streaming")
            self.assertEqual(cat("Musterfirma%"), "Gehalt")
            self.assertEqual(cat("Hausverwaltung%"), "Wohnen")
            self.assertEqual(cat("Stadtwerke%"), "Nebenkosten & Energie")
            self.assertEqual(cat("Telekom%"), "Telefon & Internet")
            self.assertEqual(cat("HUK%"), "Versicherungen")
            self.assertEqual(cat("Trade Republic%"), "Sparen & Investieren")
            self.assertEqual(cat("Max Mustermann%"), "Umbuchung")
            self.assertEqual(cat("ARAL%"), "Mobilität")
            self.assertEqual(cat("Booking%"), "Reisen")
            self.assertEqual(cat("McFit%"), "Freizeit & Unterhaltung")
            self.assertEqual(cat("Apotheke%"), "Gesundheit")
            self.assertEqual(cat("Geldautomat%"), "Bargeld")
            # Steuererstattung (Eingang) darf nicht als Ausgabe "Gebühren & Steuern" enden
            r = conn.execute("SELECT c.kind FROM transactions t JOIN categories c ON c.id=t.category_id WHERE t.payee LIKE 'Finanzamt%'").fetchone()
            self.assertEqual(r[0], "income")

    def test_word_boundaries(self):
        with session() as conn:
            cz = Categorizer(conn)
            cat = lambda p, a=-1000: conn.execute("SELECT name FROM categories WHERE id=?", (cz.categorize(p, "", "", a),)).fetchone()[0]
            self.assertEqual(cat("Foo2 GmbH"), "Sonstiges")           # "o2" darf nicht in "Foo2" matchen
            self.assertEqual(cat("O2 Telefonica"), "Telefon & Internet")
            self.assertEqual(cat("Praxis Dr. Meier"), "Gesundheit")
            self.assertEqual(cat("Überweisung an Freund"), "Sonstiges")
            self.assertEqual(cat("Unbekannt", 5000), "Sonstige Einnahmen")

    def test_manual_override_and_rule(self):
        with session() as conn:
            a, b = make_accounts(conn)
            store_transactions(conn, b, parse_csv(CD).transactions)
        c = TestClient(create_app())
        c.post("/api/login", json={"password": "geheim"})
        cats = {x["name"]: x["id"] for x in c.get("/api/categories").json()["categories"]}
        tx = next(t for t in c.get("/api/transactions?q=zalando&limit=50").json()["transactions"])
        self.assertEqual(tx["category"], "Shopping")
        r = c.patch(f"/api/transactions/{tx['id']}", json={"category_id": cats["Bildung"]})
        self.assertEqual(r.status_code, 200)
        # Neukategorisierung darf manuelle Zuordnung nicht überschreiben
        with session() as conn:
            apply_rules(conn)
        again = c.get("/api/transactions?q=zalando&limit=50").json()["transactions"]
        self.assertEqual([t for t in again if t["id"] == tx["id"]][0]["category"], "Bildung")
        # Regel aus Umsatz anlegen -> wirkt auf alle anderen Zalando-Umsätze
        r = c.patch(f"/api/transactions/{tx['id']}", json={"category_id": cats["Bildung"], "create_rule": True, "rule_pattern": "zalando"})
        self.assertGreater(r.json()["recategorized"], 0)
        others = [t for t in c.get("/api/transactions?q=zalando&limit=50").json()["transactions"]]
        self.assertTrue(all(t["category"] == "Bildung" for t in others))


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        fresh_db()
        with session() as conn:
            set_setting(conn, "own_names", "Max Mustermann")
            a, b = make_accounts(conn)
            store_transactions(conn, a, parse_csv(ING).transactions)
            store_transactions(conn, b, parse_csv(CD).transactions)
            apply_rules(conn)

    def test_overview_math(self):
        with session() as conn:
            o = analysis.overview(conn, "2026-09", today=TODAY)
            self.assertEqual(o["income_cents"], 285000)               # nur Gehalt; Umbuchungen zählen nicht
            exp = -sum(r[4] for r in ING_ROWS + CD_ROWS
                       if r[0].strftime("%Y-%m") == "2026-09" and r[4] < 0
                       and r[1] not in ("Max Mustermann", "Trade Republic Bank GmbH"))
            self.assertEqual(o["expenses_cents"], exp)
            self.assertEqual(o["transfers_cents"], 20000 + 30000 - 30000)   # Sparplan 200 € + Umbuchung hin/zurück netto 0
            self.assertAlmostEqual(o["savings_rate"], (285000 - exp) / 285000, places=3)
            self.assertEqual(sum(c["spent_cents"] for c in o["categories"]), exp)

    def test_budget_status_and_projection(self):
        with session() as conn:
            cid = conn.execute("SELECT id FROM categories WHERE name='Lebensmittel'").fetchone()[0]
            conn.execute("INSERT INTO budgets VALUES(?,?)", (cid, 5000))
            o = analysis.overview(conn, "2026-10", today=TODAY)
            c = next(x for x in o["categories"] if x["name"] == "Lebensmittel")
            self.assertEqual(c["status"], "over" if c["spent_cents"] > 5000 else "warn")
            self.assertTrue(o["is_current"])
            self.assertEqual(o["elapsed_days"], 18)
            self.assertEqual(len(o["cumulative_cents"]), 18)
            self.assertEqual(o["cumulative_cents"][-1], o["expenses_cents"])
            self.assertEqual(o["projected_expenses_cents"], round(o["expenses_cents"] / 18 * 31))

    def test_monthly_series_and_suggestions(self):
        with session() as conn:
            s = analysis.monthly_series(conn, 7, "2026-10")
            self.assertEqual([m["month"] for m in s][0], "2026-04")
            for m in s[:-1]:   # Juli enthält zusätzlich eine Steuererstattung von 312,50 €
                self.assertEqual(m["income_cents"], 285000 + (31250 if m["month"] == "2026-07" else 0), m["month"])
            sug = analysis.budget_suggestions(conn, "2026-10")
            names = {x["name"]: x["suggested_cents"] for x in sug}
            self.assertEqual(names["Wohnen"], 95000)
            self.assertTrue(all(v % 1000 == 0 for v in names.values()))

    def test_recurring(self):
        with session() as conn:
            rec = {r["payee"]: r for r in analysis.detect_recurring(conn, TODAY)}
            self.assertEqual(rec["Netflix International B.V."]["interval"], "monatlich")
            self.assertEqual(rec["Netflix International B.V."]["amount_cents"], -1299)
            self.assertEqual(rec["Hausverwaltung Müller"]["amount_cents"], -95000)
            self.assertEqual(rec["HUK-COBURG Versicherung"]["interval"], "vierteljährlich")
            self.assertEqual(rec["Musterfirma GmbH"]["amount_cents"], 285000)
            self.assertNotIn("Geldautomat", rec)                      # Bargeldabhebungen sind keine Verträge
            self.assertNotIn("REWE Markt GmbH", rec)                  # unregelmäßig -> kein Fixkosten-Treffer
            self.assertTrue(rec["Netflix International B.V."]["active"])

    def test_upcoming(self):
        with session() as conn:
            conn.execute("DELETE FROM transactions WHERE booking_date >= '2026-10-01'")
            up = {u["payee"] for u in analysis.upcoming_this_month(conn, date(2026, 10, 2))}
            self.assertIn("Netflix International B.V.", up)
            self.assertIn("Stadtwerke Musterstadt", up)
            self.assertNotIn("Hausverwaltung Müller", up)     # fällig am 1., heute ist der 2.
            self.assertNotIn("Musterfirma GmbH", up)          # Einnahme, keine Ausgabe


class ApiTests(unittest.TestCase):
    def setUp(self):
        fresh_db()
        self.c = TestClient(create_app())

    def test_auth_required(self):
        self.assertEqual(self.c.get("/api/transactions").status_code, 401)
        self.assertEqual(self.c.post("/api/login", json={"password": "falsch"}).status_code, 401)
        self.assertEqual(self.c.post("/api/login", json={"password": "geheim"}).status_code, 200)
        self.assertEqual(self.c.get("/api/transactions").status_code, 200)
        self.c.post("/api/logout")
        self.c.cookies.clear()
        self.assertEqual(self.c.get("/api/transactions").status_code, 401)

    def test_login_rate_limit(self):
        from app import main
        main._failed.clear()
        for _ in range(8):
            self.c.post("/api/login", json={"password": "x"})
        self.assertEqual(self.c.post("/api/login", json={"password": "geheim"}).status_code, 429)
        main._failed.clear()

    def test_full_flow(self):
        c = self.c
        c.post("/api/login", json={"password": "geheim"})
        a = c.post("/api/accounts", json={"name": "ING Giro", "bank": "ing"}).json()["id"]
        # Zuordnung über IBAN aus der ING-Datei (Konto hat noch keine IBAN -> Konto muss angegeben werden)
        r = c.post("/api/import", files={"file": ("ing.csv", ING, "text/csv")})
        self.assertEqual(r.status_code, 422)
        r = c.post("/api/import", files={"file": ("ing.csv", ING, "text/csv")}, data={"account_id": str(a)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["added"], len(ING_ROWS))
        self.assertEqual(r.json()["own_name_added"], "Max Mustermann")     # aus der ING-Kopfzeile "Kunde"
        self.assertEqual(c.get("/api/settings").json()["own_names"], "Max Mustermann")
        um = [t for t in c.get("/api/transactions?q=mustermann&limit=50").json()["transactions"]]
        self.assertTrue(um and all(t["category"] == "Umbuchung" for t in um))
        # IBAN wurde übernommen -> künftig automatische Zuordnung
        r = c.post("/api/import", files={"file": ("ing.csv", ING, "text/csv")})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["added"], 0)
        self.assertEqual(r.json()["duplicates"], len(ING_ROWS))
        t = c.get("/api/transactions?month=2026-10&limit=5").json()
        self.assertEqual(len(t["transactions"]), 5)
        rid = c.get("/api/categories").json()["categories"]
        lm = next(x for x in rid if x["name"] == "Lebensmittel")["id"]
        self.assertEqual(c.put(f"/api/budgets/{lm}", json={"monthly_cents": 40000}).status_code, 200)
        o = c.get("/api/analysis/overview?month=2026-09").json()
        self.assertEqual(o["income_cents"], 285000)
        self.assertEqual(next(x for x in o["categories"] if x["name"] == "Lebensmittel")["budget_cents"], 40000)
        self.assertEqual(c.get("/api/analysis/overview?month=abc").status_code, 400)
        self.assertGreater(len(c.get("/api/analysis/recurring").json()["recurring"]), 3)
        self.assertEqual(c.get("/").status_code, 200)

    def test_security_headers_and_bad_upload(self):
        c = self.c
        c.post("/api/login", json={"password": "geheim"})
        r = c.get("/api/session")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        r = c.post("/api/import", files={"file": ("x.csv", b"foo;bar\n1;2\n", "text/csv")})
        self.assertEqual(r.status_code, 400)


# ------------------------------------------------------------------ FinTS (gemockt)

class NeedTANResponse:
    def __init__(self, challenge, decoupled=False):
        self.challenge, self.decoupled = challenge, decoupled


class _Amount:
    def __init__(self, amount, currency="EUR"):
        self.amount, self.currency = amount, currency


class _Tx:
    def __init__(self, **data):
        self.data = data


class FakeClient:
    """Verhält sich wie FinTS3PinTanClient: TAN-Abfrage beim Umsatzabruf."""
    decoupled = False

    def __init__(self, blz, user, pin, url, product_id=""):
        self.init_tan_response = None
        self.sent = []

    def fetch_tan_mechanisms(self): pass
    def get_tan_mechanisms(self): return {"920": type("M", (), {"name": "pushTAN 2.0"})(), "910": type("M", (), {"name": "chipTAN"})()}
    def set_tan_mechanism(self, code): self.mech = code
    def __enter__(self): return self
    def __exit__(self, *a): return False

    def get_sepa_accounts(self):
        return [type("A", (), {"iban": "DE12500105170123456789"})()]

    def get_transactions(self, acc, start, end):
        return NeedTANResponse("Bitte freigeben" if self.decoupled else "Gib TAN 123 ein", self.decoupled)

    def send_tan(self, resp, tan):
        self.sent.append(tan)
        from decimal import Decimal
        return [
            _Tx(status="D", amount=_Amount(Decimal("12.99")), date=date(2026, 10, 12), entry_date=date(2026, 10, 12),
                applicant_name="Netflix International B.V.", purpose="Netflix", posting_text="Lastschrift", applicant_iban="NL00X"),
            _Tx(status="C", amount=_Amount(Decimal("2850.00")), date=date(2026, 9, 28), entry_date=date(2026, 9, 28),
                applicant_name="Musterfirma GmbH", purpose="Gehalt 09/2026", posting_text="Gutschrift", applicant_iban="DE99X"),
            _Tx(status="C", amount=_Amount(Decimal("300.00")), date=date(2026, 9, 10), entry_date=date(2026, 9, 10),
                applicant_name="Max M.", purpose="an mich", posting_text="Überweisung", applicant_iban="DE12500105170123456789"),
        ]


class FintsTests(unittest.TestCase):
    def setUp(self):
        fresh_db()
        with session() as conn:
            self.aid = conn.execute(
                "INSERT INTO accounts(name,bank,iban,blz,fints_url,fints_user,created_at) "
                "VALUES('ING','ing','DE12500105170123456789','50010517','https://x/fints','user1','x')").lastrowid
        self.acc = {"id": self.aid, "name": "ING", "iban": "DE12500105170123456789", "blz": "50010517",
                    "fints_url": "https://x/fints", "fints_user": "user1", "fints_tan_mechanism": None}

    def _wait(self, job, states, timeout=5):
        import time
        end = time.time() + timeout
        while time.time() < end:
            if job.state in states:
                return
            time.sleep(0.02)
        self.fail(f"Job-Status {job.state!r}, erwartet {states}: {job.message}")

    def test_tan_flow(self):
        job = fints_sync.start_sync(self.acc, "1234", 30, client_factory=FakeClient)
        self._wait(job, {"need_tan"})
        self.assertIn("TAN", job.challenge)
        fints_sync.submit_tan(job.id, "987654")
        self._wait(job, {"done"})
        self.assertEqual(job.result["added"], 3)
        with session() as conn:
            rows = {r["payee"]: r for r in conn.execute("SELECT t.*, c.name cat FROM transactions t JOIN categories c ON c.id=t.category_id")}
            self.assertEqual(rows["Netflix International B.V."]["amount_cents"], -1299)
            self.assertEqual(rows["Musterfirma GmbH"]["amount_cents"], 285000)
            self.assertEqual(rows["Max M."]["cat"], "Umbuchung")   # Gegen-IBAN = eigenes Konto
            self.assertIsNotNone(conn.execute("SELECT last_sync FROM accounts").fetchone()[0])

    def test_decoupled_pushtan(self):
        class Push(FakeClient):
            decoupled = True
        job = fints_sync.start_sync(self.acc, "1234", 30, client_factory=Push)
        self._wait(job, {"need_approval"})
        fints_sync.submit_tan(job.id, "")
        self._wait(job, {"done"})

    def test_error_hides_pin(self):
        class Boom(FakeClient):
            def get_sepa_accounts(self): raise RuntimeError("Login fehlgeschlagen für PIN 4711")
        job = fints_sync.start_sync(self.acc, "4711", 30, client_factory=Boom)
        self._wait(job, {"error"})
        self.assertNotIn("4711", job.message)

    def test_wrong_iban(self):
        self.acc["iban"] = "DE00000000000000000000"
        job = fints_sync.start_sync(self.acc, "1", 30, client_factory=FakeClient)
        self._wait(job, {"error"})
        self.assertIn("IBAN", job.message)

    def test_requires_product_id(self):
        os.environ.pop("FINTS_PRODUCT_ID", None)
        job = fints_sync.start_sync(self.acc, "1", 30)   # echte Factory
        self._wait(job, {"error"})
        self.assertTrue("FINTS_PRODUCT_ID" in job.message or "fints" in job.message)


# ------------------------------------------------------------------ comdirect REST-API (gemockt)

import json as _json  # noqa: E402
from app import comdirect_api, comdirect_sync  # noqa: E402


def make_http(push=True, log=None):
    """Simuliert die comdirect-API entlang des dokumentierten Ablaufs."""
    log = log if log is not None else []
    tx_pages = [
        [{"bookingDate": "2026-10-12", "valutaDate": "2026-10-12", "amount": {"value": "-12.99", "unit": "EUR"},
          "remittanceInfo": "Netflix Abo", "transactionType": {"key": "DIRECT_DEBIT", "text": "Lastschrift"},
          "creditor": {"holderName": "Netflix International B.V."}},
         {"bookingDate": "2026-09-28", "valutaDate": "2026-09-28", "amount": {"value": "2850.00", "unit": "EUR"},
          "remittanceInfo": "Gehalt 09/2026", "transactionType": {"key": "TRANSFER", "text": "Überweisung"},
          "remitter": {"holderName": "Musterfirma GmbH"}}],
        [{"bookingDate": "2026-09-10", "amount": {"value": "-5.00", "unit": "EUR"}, "remittanceInfo": "x",
          "transactionType": {"text": "Lastschrift"}, "creditor": {"holderName": "REWE Markt"}}],
    ]

    def http(method, url, headers, data):
        log.append((method, url, dict(headers), data))
        assert "x-http-request-info" in headers
        if url.endswith("/oauth/token"):
            f = dict(x.split("=") for x in data.decode().split("&"))
            if f["grant_type"] == "password":
                assert f["username"] == "1234567" and f["password"] == "4711"
                return 200, {}, _json.dumps({"access_token": "A1", "refresh_token": "R1"}).encode()
            assert f["grant_type"] == "cd_secondary" and f["token"] == "A1"
            return 200, {}, _json.dumps({"access_token": "A2", "refresh_token": "R2"}).encode()
        if url.endswith("/sessions") and method == "GET":
            assert headers["Authorization"] == "Bearer A1"
            return 200, {}, _json.dumps([{"identifier": "SESS1"}]).encode()
        if url.endswith("/validate"):
            info = {"id": "CH1", "typ": "P_TAN_PUSH" if push else "M_TAN", "challenge": None if push else "SMS an 0151***"}
            return 201, {"x-once-authentication-info": _json.dumps(info)}, b"{}"
        if method == "PATCH":
            if not push:
                assert headers["x-once-authentication"] == "123456"
            return 200, {}, b"{}"
        if url.endswith("/balances"):
            assert headers["Authorization"] == "Bearer A2"
            return 200, {}, _json.dumps({"values": [{"accountId": "UUID-1", "account": {"iban": "DE99 2004 1155 0000 0000 00"}}]}).encode()
        if "/transactions?" in url:
            assert headers["Authorization"] == "Bearer A2" and "transactionState=BOOKED" in url
            first = int(url.split("paging-first=")[1].split("&")[0])
            page = tx_pages[0] if first == 0 else tx_pages[1] if first == 2 else []
            return 200, {}, _json.dumps({"values": page, "paging": {"matches": 3}}).encode()
        if url.endswith("/oauth/revoke"):
            return 204, {}, b""
        return 500, {"x-http-response-info": _json.dumps({"messages": [{"message": "unerwartet " + url}]})}, b""
    return http


class ComdirectApiTests(unittest.TestCase):
    def setUp(self):
        fresh_db()
        os.environ["COMDIRECT_CLIENT_ID"], os.environ["COMDIRECT_CLIENT_SECRET"] = "cid", "sec"
        with session() as conn:
            self.aid = conn.execute("INSERT INTO accounts(name,bank,iban,fints_user,created_at) "
                                    "VALUES('cd','comdirect','DE99200411550000000000','1234567','x')").lastrowid
        self.acc = {"id": self.aid, "iban": "DE99200411550000000000", "fints_user": "1234567", "fints_tan_mechanism": None}

    def _wait(self, job, states):
        import time
        end = time.time() + 5
        while time.time() < end:
            if job.state in states:
                return
            time.sleep(0.02)
        self.fail(f"{job.state}: {job.message}")

    def _factory(self, push=True, log=None):
        http = make_http(push, log)
        return lambda cid, sec: comdirect_api.ComdirectClient(cid, sec, http=http)

    def test_push_flow_paging_and_revoke(self):
        log = []
        job = comdirect_sync.start_sync(self.acc, "4711", 90, self._factory(True, log))
        self._wait(job, {"need_approval"})
        comdirect_sync.fints_sync.submit_tan(job.id, "")
        self._wait(job, {"done"})
        self.assertEqual(job.result["added"], 3)                       # 2 + 1 über zwei Seiten
        self.assertTrue(log[-1][1].endswith("/oauth/revoke"))          # Token wird am Ende widerrufen
        self.assertTrue(all(m == "GET" or "/oauth/" in u or "sessions" in u for m, u, *_ in log))  # nur lesend
        with session() as conn:
            r = {x["payee"]: x for x in conn.execute("SELECT t.*, c.name cat FROM transactions t JOIN categories c ON c.id=t.category_id")}
            self.assertEqual(r["Netflix International B.V."]["amount_cents"], -1299)
            self.assertEqual(r["Netflix International B.V."]["cat"], "Abos & Streaming")
            self.assertEqual(r["Musterfirma GmbH"]["amount_cents"], 285000)
            self.assertEqual(r["Musterfirma GmbH"]["cat"], "Gehalt")

    def test_mtan_flow(self):
        job = comdirect_sync.start_sync(self.acc, "4711", 90, self._factory(False))
        self._wait(job, {"need_tan"})
        self.assertIn("SMS", job.challenge or job.message)
        comdirect_sync.fints_sync.submit_tan(job.id, "123456")
        self._wait(job, {"done"})

    def test_api_error_message_and_no_pin_leak(self):
        def bad(method, url, headers, data):
            return 401, {"x-http-response-info": _json.dumps({"messages": [{"message": "Zugang gesperrt, PIN 4711"}]})}, b""
        job = comdirect_sync.start_sync(self.acc, "4711", 90, lambda c, s: comdirect_api.ComdirectClient(c, s, http=bad))
        self._wait(job, {"error"})
        self.assertIn("gesperrt", job.message)
        self.assertNotIn("4711", job.message)

    def test_csv_then_api_no_duplicates(self):
        """Gleiche Buchung erst per CSV (anderer Text), dann per API: darf nicht doppelt erscheinen."""
        from app.importers import ParsedTx
        with session() as conn:
            store_transactions(conn, self.aid, [ParsedTx("2026-10-12", None, "NETFLIX INTL", "Abo Oktober", "Lastschrift / Belastung", -1299)])
        job = comdirect_sync.start_sync(self.acc, "4711", 90, self._factory(True))
        self._wait(job, {"need_approval"})
        comdirect_sync.fints_sync.submit_tan(job.id, "")
        self._wait(job, {"done"})
        with session() as conn:
            n = conn.execute("SELECT COUNT(*) FROM transactions WHERE booking_date='2026-10-12' AND amount_cents=-1299").fetchone()[0]
            self.assertEqual(n, 1)
        self.assertEqual(job.result["added"], 2)
        self.assertEqual(job.result["duplicates"], 1)

    def test_endpoint_prefers_comdirect_api(self):
        c = TestClient(create_app())
        c.post("/api/login", json={"password": "geheim"})
        self.assertTrue(c.get("/api/accounts").json()["comdirect_api_enabled"])
        orig = comdirect_sync.ComdirectClient
        comdirect_sync.ComdirectClient = lambda cid, sec: comdirect_api.ComdirectClient(cid, sec, http=make_http(True))
        try:
            # start_sync bindet den Default beim Aufruf -> über main gehen wir den echten Pfad
            r = c.post(f"/api/accounts/{self.aid}/sync", json={"pin": "4711", "days": 30})
            self.assertEqual(r.status_code, 202)
        finally:
            comdirect_sync.ComdirectClient = orig


if __name__ == "__main__":
    unittest.main()
