"""Starlette-App: JSON-API + statische Oberfläche. Einzelnutzer mit Passwort-Login."""
from __future__ import annotations

import hmac
import os
import re
import secrets
import time
from datetime import date

from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import analysis, category_tree, comdirect_api, comdirect_sync, fints_sync
from .categorizer import apply_rules
from .db import get_setting, init_db, now_iso, session, set_setting
from .importers import ImportError_, find_account_by_iban, parse_csv, remove_duplicates, store_transactions

STATIC = os.path.join(os.path.dirname(__file__), "static")
COOKIE = "finanz_session"
SESSION_MAX_AGE = 60 * 60 * 12


def _secret_key() -> str:
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(os.path.dirname(os.environ.get("FINANZ_DB", "data/finanz.db")) or ".", "secret.key")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        return open(path).read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    os.chmod(path, 0o600)
    return key


def _password() -> str:
    return os.environ.get("APP_PASSWORD", "")


def _trust_ingress() -> bool:
    """Im Home-Assistant-Add-on (TRUST_INGRESS=1) authentifiziert HA den Nutzer bereits."""
    return os.environ.get("TRUST_INGRESS", "0") == "1"


def _via_ingress(request: Request) -> bool:
    """Ingress-Anfragen kommen ausschließlich vom HA-Supervisor (172.30.32.2).
    Direkte Zugriffe (z. B. über einen freigegebenen Port) zählen nicht dazu."""
    ingress_ip = os.environ.get("INGRESS_IP", "172.30.32.2")
    return _trust_ingress() and request.client is not None and request.client.host == ingress_ip


_failed: dict[str, list[float]] = {}


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret_key(), salt="finanz-session")


def _authed(request: Request) -> bool:
    if _via_ingress(request):
        return True
    tok = request.cookies.get(COOKIE)
    if not tok or not _password():
        return False
    try:
        _serializer().loads(tok, max_age=SESSION_MAX_AGE)
        return True
    except BadSignature:
        return False


class AuthMiddleware(BaseHTTPMiddleware):
    OPEN = {"/api/login", "/api/session", "/", "/login", "/healthz"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in self.OPEN and not _authed(request):
            return JSONResponse({"error": "Nicht angemeldet"}, status_code=401)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        # SAMEORIGIN statt DENY: Home Assistant bettet das Add-on per Ingress als iframe (gleiche Origin) ein.
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'")
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp


def err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


async def body(request: Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ------------------------------------------------------------------ Auth

async def login(request: Request):
    if not _password():
        return err("Direktzugriff ist deaktiviert, weil kein Passwort gesetzt ist. Öffne die App über die "
                   "Home-Assistant-Seitenleiste oder setze in der Add-on-Konfiguration ein Passwort.", 403)
    ip = request.client.host if request.client else "?"
    now = time.time()
    attempts = [t for t in _failed.get(ip, []) if now - t < 600]
    if len(attempts) >= 8:
        return err("Zu viele Fehlversuche. Bitte später erneut versuchen.", 429)
    data = await body(request)
    if hmac.compare_digest(str(data.get("password", "")).encode(), _password().encode()):
        _failed.pop(ip, None)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, _serializer().dumps("ok"), max_age=SESSION_MAX_AGE, httponly=True,
                        samesite="strict", secure=os.environ.get("COOKIE_SECURE", "0") == "1")
        return resp
    attempts.append(now)
    _failed[ip] = attempts
    return err("Falsches Passwort.", 401)


async def logout(request: Request):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


async def session_info(request: Request):
    return JSONResponse({"authenticated": _authed(request), "ingress": _via_ingress(request),
                         "login_available": bool(_password())})


# ------------------------------------------------------------------ Konten

def _account_row(r) -> dict:
    d = dict(r)
    d["tx_count"] = r["tx_count"] if "tx_count" in r.keys() else 0
    return d


async def list_accounts(request: Request):
    with session() as conn:
        rows = conn.execute(
            "SELECT a.*, (SELECT COUNT(*) FROM transactions t WHERE t.account_id=a.id) AS tx_count, "
            "(SELECT COALESCE(SUM(amount_cents),0) FROM transactions t WHERE t.account_id=a.id) AS sum_cents "
            "FROM accounts a ORDER BY a.id").fetchall()
        return JSONResponse({
            "accounts": [_account_row(r) for r in rows],
            "presets": fints_sync.BANK_PRESETS,
            "fints_enabled": bool(os.environ.get("FINTS_PRODUCT_ID")),
            "comdirect_api_enabled": comdirect_api.configured(),
        })


async def create_account(request: Request):
    d = await body(request)
    name = str(d.get("name", "")).strip()
    bank = d.get("bank") if d.get("bank") in ("ing", "comdirect", "other") else "other"
    if not name:
        return err("Name fehlt.")
    preset = fints_sync.BANK_PRESETS.get(bank, {})
    with session() as conn:
        cur = conn.execute(
            "INSERT INTO accounts(name,bank,iban,blz,fints_url,fints_user,fints_tan_mechanism,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (name, bank, (d.get("iban") or "").replace(" ", "").upper() or None,
             d.get("blz") or preset.get("blz"), d.get("fints_url") or preset.get("url"),
             d.get("fints_user") or None, d.get("fints_tan_mechanism") or None, now_iso()))
        return JSONResponse({"id": cur.lastrowid}, status_code=201)


async def update_account(request: Request):
    aid = int(request.path_params["id"])
    d = await body(request)
    fields = {k: d[k] for k in ("name", "iban", "blz", "fints_url", "fints_user", "fints_tan_mechanism") if k in d}
    if "iban" in fields and fields["iban"]:
        fields["iban"] = re.sub(r"\s+", "", fields["iban"]).upper()
    if not fields:
        return err("Nichts zu ändern.")
    with session() as conn:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE accounts SET {sets} WHERE id=?", (*[v or None for v in fields.values()], aid))
    return JSONResponse({"ok": True})


async def delete_account(request: Request):
    aid = int(request.path_params["id"])
    with session() as conn:
        conn.execute("DELETE FROM accounts WHERE id=?", (aid,))
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------ Import / Sync

async def import_csv(request: Request):
    form = await request.form()
    up = form.get("file")
    if up is None or not hasattr(up, "read"):
        return err("Keine Datei übergeben.")
    data = await up.read()
    if len(data) > 20 * 1024 * 1024:
        return err("Datei zu groß (max. 20 MB).", 413)
    try:
        pf = parse_csv(data)
    except ImportError_ as e:
        return err(str(e))
    with session() as conn:
        aid = int(form["account_id"]) if form.get("account_id") else None
        if aid is None:
            aid = find_account_by_iban(conn, pf.iban)
        if aid is None:
            return err("Konto konnte nicht zugeordnet werden – bitte Konto auswählen.", 422)
        acc = conn.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
        if acc is None:
            return err("Konto nicht gefunden.", 404)
        if pf.iban and not acc["iban"]:
            conn.execute("UPDATE accounts SET iban=? WHERE id=?", (re.sub(r"\s+", "", pf.iban).upper(), aid))
        # Kontoinhaber als "eigenen Namen" merken, damit Überweisungen zwischen eigenen Konten
        # (z. B. ING -> comdirect) als Umbuchung und nicht als Ausgabe/Einnahme zählen.
        name_added = None
        if pf.holder:
            names = [n.strip() for n in get_setting(conn, "own_names", "").split(",") if n.strip()]
            if pf.holder.lower() not in (n.lower() for n in names):
                set_setting(conn, "own_names", ", ".join(names + [pf.holder]))
                name_added = pf.holder
        stats = store_transactions(conn, aid, pf.transactions, fuzzy=True)   # auch gegen FinTS-Umsätze abgleichen
        if name_added:
            apply_rules(conn)
        return JSONResponse({**stats, "bank": pf.bank, "account_id": aid, "parsed": len(pf.transactions),
                             "own_name_added": name_added})


async def sync_start(request: Request):
    aid = int(request.path_params["id"])
    d = await body(request)
    pin = str(d.get("pin", ""))
    if not pin:
        return err("PIN fehlt (wird nicht gespeichert).")
    with session() as conn:
        acc = conn.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
    if not acc:
        return err("Konto nicht gefunden.", 404)
    try:
        days = int(d.get("days", 90))
        if acc["bank"] == "comdirect" and comdirect_api.configured():
            job = comdirect_sync.start_sync(dict(acc), pin, days)      # bevorzugt: offizielle REST-API
        else:
            job = fints_sync.start_sync(dict(acc), pin, days)
    except ValueError as e:
        return err(str(e))
    return JSONResponse(job.public(), status_code=202)


async def dedupe(request: Request):
    with session() as conn:
        n = remove_duplicates(conn)
    return JSONResponse({"removed": n})


async def sync_status(request: Request):
    job = fints_sync.JOBS.get(request.path_params["job"])
    if not job:
        return err("Job unbekannt.", 404)
    return JSONResponse(job.public())


async def sync_tan(request: Request):
    d = await body(request)
    try:
        job = fints_sync.submit_tan(request.path_params["job"], str(d.get("tan", "")))
    except KeyError:
        return err("Job unbekannt.", 404)
    except ValueError as e:
        return err(str(e), 409)
    return JSONResponse(job.public())


# ------------------------------------------------------------------ Umsätze

def _tx_filter(p) -> tuple[str, list]:
    """WHERE-Klausel (Alias t) aus den Filter-Parametern der Umsatzliste."""
    where, args = ["1=1"], []
    if p.get("from"):
        where.append("t.booking_date >= ?"); args.append(p["from"])
    if p.get("to"):
        where.append("t.booking_date <= ?"); args.append(p["to"])
    if p.get("month"):
        s, e = analysis.month_bounds(p["month"])
        where += ["t.booking_date >= ?", "t.booking_date <= ?"]; args += [s, e]
    if p.get("account_id"):
        where.append("t.account_id=?"); args.append(int(p["account_id"]))
    if p.get("category_id") == "none":
        where.append("t.category_id IS NULL")
    elif p.get("category_id"):
        with session() as _c:                       # Oberkategorie schließt ihre Unterkategorien ein
            ids = category_tree.descendants(_c, int(p["category_id"]))
        where.append(f"t.category_id IN ({','.join('?' * len(ids))})"); args += ids
    if p.get("q"):
        like = f"%{p['q'].lower()}%"
        where.append("(LOWER(t.payee) LIKE ? OR LOWER(t.purpose) LIKE ? OR LOWER(t.booking_text) LIKE ?)")
        args += [like, like, like]
    return " AND ".join(where), args


async def list_transactions(request: Request):
    p = request.query_params
    w, args = _tx_filter(p)
    limit = min(int(p.get("limit", 100)), 500)
    offset = int(p.get("offset", 0))
    asc = p.get("dir") == "asc"
    d = "ASC" if asc else "DESC"
    order = {"amount": f"t.amount_cents {d}, t.booking_date DESC, t.id DESC"}.get(
        p.get("sort"), f"t.booking_date {d}, t.id {d}")                   # Whitelist, kein SQL aus Parametern
    with session() as conn:
        total = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(t.amount_cents),0) FROM transactions t WHERE {w}", args).fetchone()
        rows = conn.execute(
            f"SELECT t.*, a.name AS account_name, c.name AS category, c.color AS color "
            f"FROM transactions t JOIN accounts a ON a.id=t.account_id "
            f"LEFT JOIN categories c ON c.id=t.category_id WHERE {w} "
            f"ORDER BY {order} LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return JSONResponse({"total": total[0], "sum_cents": total[1],
                             "transactions": [{k: r[k] for k in r.keys() if k not in ("hash",)} for r in rows]})


async def delete_transactions(request: Request):
    """Löscht alle Umsätze, die zu den Filtern passen. `expected` muss der angezeigten Anzahl entsprechen."""
    p = request.query_params
    try:
        expected = int(p.get("expected", ""))
    except ValueError:
        return err("expected (erwartete Anzahl) fehlt.")
    w, args = _tx_filter(p)
    with session() as conn:
        n = conn.execute(f"SELECT COUNT(*) FROM transactions t WHERE {w}", args).fetchone()[0]
        if n != expected:
            return err(f"Die Auswahl hat sich geändert ({n} statt {expected} Umsätze). Bitte neu laden.", 409)
        conn.execute(f"DELETE FROM transactions WHERE id IN (SELECT t.id FROM transactions t WHERE {w})", args)
    return JSONResponse({"deleted": n})


async def patch_transaction(request: Request):
    tid = int(request.path_params["id"])
    d = await body(request)
    if "category_id" not in d:
        return err("category_id fehlt.")
    with session() as conn:
        t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
        if not t:
            return err("Umsatz nicht gefunden.", 404)
        conn.execute("UPDATE transactions SET category_id=?, category_source='manual' WHERE id=?",
                     (d["category_id"], tid))
        rule_created, changed = None, 0
        pattern = str(d.get("rule_pattern", "")).strip()
        if d.get("create_rule") and pattern:
            direction = "in" if t["amount_cents"] > 0 else "out"
            cur = conn.execute(
                "INSERT INTO rules(category_id,pattern,direction,priority,source,created_at) VALUES(?,?,?,?, 'user', ?)",
                (d["category_id"], pattern, direction, 10, now_iso()))
            rule_created = cur.lastrowid
            changed = apply_rules(conn)
        return JSONResponse({"ok": True, "rule_id": rule_created, "recategorized": changed})


# ------------------------------------------------------------------ Kategorien / Regeln / Budgets

async def list_categories(request: Request):
    with session() as conn:
        return JSONResponse({"categories": [dict(r) for r in conn.execute(
            "SELECT c.*, b.monthly_cents AS budget_cents FROM categories c "
            "LEFT JOIN budgets b ON b.category_id=c.id ORDER BY c.kind DESC, c.name")]})


async def create_category(request: Request):
    d = await body(request)
    name = str(d.get("name", "")).strip()
    kind = d.get("kind", "expense")
    parent_id = d.get("parent_id") or None
    if not name:
        return err("Name fehlt.")
    with session() as conn:
        color = d.get("color")
        if parent_id:
            p = conn.execute("SELECT * FROM categories WHERE id=?", (int(parent_id),)).fetchone()
            if not p:
                return err("Oberkategorie nicht gefunden.", 404)
            if p["parent_id"]:
                return err("Unterkategorien können nicht weiter verschachtelt werden.")
            kind, color = p["kind"], color or p["color"]       # erbt Art und Farbe
        if kind not in ("expense", "income", "transfer"):
            return err("Art ungültig.")
        try:
            cur = conn.execute("INSERT INTO categories(name,kind,color,parent_id) VALUES(?,?,?,?)",
                               (name, kind, color or "#9aa0a6", parent_id))
        except Exception:  # noqa: BLE001
            return err("Kategorie existiert bereits.", 409)
        return JSONResponse({"id": cur.lastrowid}, status_code=201)


async def update_category(request: Request):
    """Umbenennen, Farbe ändern oder einer anderen Oberkategorie zuordnen (parent_id=null: oberste Ebene)."""
    cid = int(request.path_params["id"])
    d = await body(request)
    with session() as conn:
        c = conn.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
        if not c:
            return err("Kategorie nicht gefunden.", 404)
        name = str(d.get("name", c["name"])).strip()
        if not name:
            return err("Name fehlt.")
        color = d.get("color") or c["color"]
        parent_id = c["parent_id"]
        if "parent_id" in d:
            parent_id = d["parent_id"] or None
            if parent_id:
                parent_id = int(parent_id)
                p = conn.execute("SELECT * FROM categories WHERE id=?", (parent_id,)).fetchone()
                if not p or parent_id == cid:
                    return err("Oberkategorie ungültig.")
                if p["parent_id"]:
                    return err("Unterkategorien können nicht weiter verschachtelt werden.")
                if p["kind"] != c["kind"]:
                    return err("Ober- und Unterkategorie müssen dieselbe Art haben (Ausgabe/Einnahme/Umbuchung).")
                if conn.execute("SELECT 1 FROM categories WHERE parent_id=?", (cid,)).fetchone():
                    return err("Diese Kategorie hat selbst Unterkategorien und kann nicht untergeordnet werden.")
        try:
            conn.execute("UPDATE categories SET name=?, color=?, parent_id=? WHERE id=?", (name, color, parent_id, cid))
        except Exception:  # noqa: BLE001
            return err("Kategorie existiert bereits.", 409)
    return JSONResponse({"ok": True})


async def apply_category_template(request: Request):
    with session() as conn:
        res = category_tree.apply_template(conn)
        res["recategorized"] = apply_rules(conn)
    return JSONResponse(res)


async def delete_category(request: Request):
    cid = int(request.path_params["id"])
    with session() as conn:
        row = conn.execute("SELECT parent_id FROM categories WHERE id=?", (cid,)).fetchone()
        if row and row["parent_id"]:      # Umsätze einer gelöschten Unterkategorie wandern zur Oberkategorie
            conn.execute("UPDATE transactions SET category_id=? WHERE category_id=?", (row["parent_id"], cid))
        conn.execute("DELETE FROM categories WHERE id=?", (cid,))   # Unterkategorien rücken auf die oberste Ebene
        apply_rules(conn)
    return JSONResponse({"ok": True})


async def list_rules(request: Request):
    with session() as conn:
        rows = conn.execute(
            "SELECT r.*, c.name AS category FROM rules r JOIN categories c ON c.id=r.category_id "
            "WHERE r.source='user' ORDER BY r.id DESC").fetchall()
        n_default = conn.execute("SELECT COUNT(*) FROM rules WHERE source='default'").fetchone()[0]
        return JSONResponse({"rules": [dict(r) for r in rows], "default_rule_count": n_default})


async def create_rule(request: Request):
    d = await body(request)
    pattern = str(d.get("pattern", "")).strip()
    if not pattern or not d.get("category_id"):
        return err("Muster und Kategorie nötig.")
    if pattern.lower().startswith("re:"):
        try:
            re.compile(pattern[3:])
        except re.error as e:
            return err(f"Ungültige Regex: {e}")
    direction = d.get("direction") if d.get("direction") in ("any", "in", "out") else "any"
    with session() as conn:
        cur = conn.execute(
            "INSERT INTO rules(category_id,pattern,direction,priority,source,created_at) VALUES(?,?,?,?, 'user', ?)",
            (d["category_id"], pattern, direction, int(d.get("priority", 10)), now_iso()))
        changed = apply_rules(conn)
        return JSONResponse({"id": cur.lastrowid, "recategorized": changed}, status_code=201)


async def delete_rule(request: Request):
    with session() as conn:
        conn.execute("DELETE FROM rules WHERE id=? AND source='user'", (int(request.path_params["id"]),))
        changed = apply_rules(conn)
    return JSONResponse({"ok": True, "recategorized": changed})


async def reapply_rules(request: Request):
    with session() as conn:
        return JSONResponse({"recategorized": apply_rules(conn)})


async def get_settings(request: Request):
    with session() as conn:
        return JSONResponse({"own_names": get_setting(conn, "own_names", "")})


async def put_settings(request: Request):
    d = await body(request)
    with session() as conn:
        if "own_names" in d:
            set_setting(conn, "own_names", str(d["own_names"]))
        return JSONResponse({"ok": True, "recategorized": apply_rules(conn)})


async def put_budget(request: Request):
    cid = int(request.path_params["id"])
    d = await body(request)
    cents = d.get("monthly_cents")
    with session() as conn:
        if cents in (None, 0, ""):
            conn.execute("DELETE FROM budgets WHERE category_id=?", (cid,))
        else:
            conn.execute("INSERT INTO budgets(category_id,monthly_cents) VALUES(?,?) "
                         "ON CONFLICT(category_id) DO UPDATE SET monthly_cents=excluded.monthly_cents",
                         (cid, int(cents)))
    return JSONResponse({"ok": True})


async def budget_suggestions(request: Request):
    with session() as conn:
        return JSONResponse({"suggestions": analysis.budget_suggestions(conn, request.query_params.get("month"))})


# ------------------------------------------------------------------ Analysen

def _month_param(request: Request) -> str:
    m = request.query_params.get("month") or date.today().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}(-(0[1-9]|1[0-2]))?", m):
        raise ValueError("month muss YYYY-MM oder YYYY sein")
    return m


async def api_overview(request: Request):
    try:
        month = _month_param(request)
    except ValueError as e:
        return err(str(e))
    acc = request.query_params.get("account_id")
    with session() as conn:
        data = analysis.overview(conn, month, account_id=int(acc) if acc else None)
        if data["is_current"] and not data["is_year"]:
            data["upcoming"] = analysis.upcoming_this_month(conn)
        return JSONResponse(data)


async def api_monthly(request: Request):
    acc = request.query_params.get("account_id")
    with session() as conn:
        end = request.query_params.get("end_month")
        if end and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", end):
            return err("end_month muss YYYY-MM sein.")
        return JSONResponse({"months": analysis.monthly_series(
            conn, int(request.query_params.get("months", 12)), end_month=end or None,
            account_id=int(acc) if acc else None)})


async def api_recurring(request: Request):
    with session() as conn:
        items = analysis.detect_recurring(conn)
    active = [i for i in items if i["active"]]
    return JSONResponse({
        "recurring": items,
        "monthly_fixed_cents": sum(i["monthly_cents"] for i in active if i["monthly_cents"] < 0),
        "monthly_fixed_income_cents": sum(i["monthly_cents"] for i in active if i["monthly_cents"] > 0),
    })


async def index(request: Request):
    return FileResponse(os.path.join(STATIC, "index.html"))


async def healthz(request: Request):
    return Response("ok")


routes = [
    Route("/", index),
    Route("/healthz", healthz),
    Route("/api/login", login, methods=["POST"]),
    Route("/api/logout", logout, methods=["POST"]),
    Route("/api/session", session_info),
    Route("/api/accounts", list_accounts),
    Route("/api/accounts", create_account, methods=["POST"]),
    Route("/api/accounts/{id:int}", update_account, methods=["PATCH"]),
    Route("/api/accounts/{id:int}", delete_account, methods=["DELETE"]),
    Route("/api/import", import_csv, methods=["POST"]),
    Route("/api/accounts/{id:int}/sync", sync_start, methods=["POST"]),
    Route("/api/dedupe", dedupe, methods=["POST"]),
    Route("/api/sync/{job}", sync_status),
    Route("/api/sync/{job}/tan", sync_tan, methods=["POST"]),
    Route("/api/transactions", list_transactions),
    Route("/api/transactions", delete_transactions, methods=["DELETE"]),
    Route("/api/transactions/{id:int}", patch_transaction, methods=["PATCH"]),
    Route("/api/categories", list_categories),
    Route("/api/categories", create_category, methods=["POST"]),
    Route("/api/categories/template", apply_category_template, methods=["POST"]),
    Route("/api/categories/{id:int}", update_category, methods=["PATCH"]),
    Route("/api/categories/{id:int}", delete_category, methods=["DELETE"]),
    Route("/api/rules", list_rules),
    Route("/api/rules", create_rule, methods=["POST"]),
    Route("/api/rules/apply", reapply_rules, methods=["POST"]),
    Route("/api/rules/{id:int}", delete_rule, methods=["DELETE"]),
    Route("/api/settings", get_settings),
    Route("/api/settings", put_settings, methods=["PUT"]),
    Route("/api/budgets/suggestions", budget_suggestions),
    Route("/api/budgets/{id:int}", put_budget, methods=["PUT"]),
    Route("/api/analysis/overview", api_overview),
    Route("/api/analysis/monthly", api_monthly),
    Route("/api/analysis/recurring", api_recurring),
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
]


def create_app() -> Starlette:
    if not _password() and not _trust_ingress():
        # Ohne Passwort und ohne Ingress-Vertrauen wäre die App ungeschützt erreichbar.
        raise RuntimeError("APP_PASSWORD ist nicht gesetzt. Ohne Passwort startet die App aus Sicherheitsgründen nicht.")
    init_db()
    return Starlette(routes=routes, middleware=[Middleware(AuthMiddleware)])


app = create_app()
