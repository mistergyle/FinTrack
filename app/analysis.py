"""Budget- und Ausgabenanalysen, Fixkosten-/Abo-Erkennung."""
from __future__ import annotations

import calendar
import re
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, timedelta


def month_bounds(month: str) -> tuple[str, str]:
    y, m = map(int, month.split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"


def shift_month(month: str, delta: int) -> str:
    y, m = map(int, month.split("-"))
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _spend_by_category(conn: sqlite3.Connection, start: str, end: str, account_id: int | None = None):
    q = ("SELECT c.id, c.name, c.kind, c.color, SUM(t.amount_cents) AS s "
         "FROM transactions t JOIN categories c ON c.id=t.category_id "
         "WHERE t.booking_date BETWEEN ? AND ?")
    args: list = [start, end]
    if account_id:
        q += " AND t.account_id=?"
        args.append(account_id)
    q += " GROUP BY c.id"
    return conn.execute(q, args).fetchall()


def overview(conn: sqlite3.Connection, month: str, today: date | None = None,
             account_id: int | None = None) -> dict:
    today = today or date.today()
    start, end = month_bounds(month)
    rows = _spend_by_category(conn, start, end, account_id)
    budgets = {r["category_id"]: r["monthly_cents"] for r in conn.execute("SELECT * FROM budgets")}

    # Durchschnitt der letzten 3 vollen Monate je Kategorie (Vergleichswert)
    prev = [shift_month(month, -i) for i in (1, 2, 3)]
    avg: dict[int, float] = defaultdict(float)
    for pm in prev:
        ps, pe = month_bounds(pm)
        for r in _spend_by_category(conn, ps, pe, account_id):
            if r["kind"] == "expense":
                avg[r["id"]] += -r["s"] / 3

    y, m = map(int, month.split("-"))
    days_in_month = calendar.monthrange(y, m)[1]
    is_current = (today.year, today.month) == (y, m)
    is_future = (today.year, today.month) < (y, m)
    elapsed = today.day if is_current else (0 if is_future else days_in_month)

    income = 0
    expenses = 0
    transfers_out = 0
    categories = []
    for r in rows:
        if r["kind"] == "income":
            income += r["s"]
        elif r["kind"] == "expense":
            expenses += -r["s"]
        else:
            transfers_out += -r["s"]
    for r in rows:
        if r["kind"] != "expense":
            continue
        spent = -r["s"]
        budget = budgets.get(r["id"])
        projected = round(spent / elapsed * days_in_month) if is_current and elapsed else spent
        status = "none"
        if budget:
            if spent > budget:
                status = "over"
            elif projected > budget:
                status = "warn"
            else:
                status = "ok"
        categories.append({
            "id": r["id"], "name": r["name"], "color": r["color"],
            "spent_cents": spent, "budget_cents": budget,
            "projected_cents": projected, "avg3_cents": round(avg.get(r["id"], 0)),
            "status": status,
        })
    # Kategorien mit Budget aber ohne Umsatz im Monat ebenfalls zeigen
    shown = {c["id"] for c in categories}
    for cid, b in budgets.items():
        if cid in shown:
            continue
        c = conn.execute("SELECT id,name,kind,color FROM categories WHERE id=?", (cid,)).fetchone()
        if c and c["kind"] == "expense":
            categories.append({"id": c["id"], "name": c["name"], "color": c["color"],
                               "spent_cents": 0, "budget_cents": b, "projected_cents": 0,
                               "avg3_cents": round(avg.get(cid, 0)), "status": "ok"})
    categories.sort(key=lambda c: -c["spent_cents"])

    # Kumulierte Ausgaben pro Tag
    q = ("SELECT t.booking_date d, SUM(-t.amount_cents) s FROM transactions t "
         "JOIN categories c ON c.id=t.category_id WHERE c.kind='expense' "
         "AND t.booking_date BETWEEN ? AND ?")
    args: list = [start, end]
    if account_id:
        q += " AND t.account_id=?"
        args.append(account_id)
    q += " GROUP BY t.booking_date"
    per_day = {r["d"]: r["s"] for r in conn.execute(q, args)}
    cumulative, run = [], 0
    last_day = elapsed if (is_current or is_future) else days_in_month
    for d in range(1, last_day + 1):
        run += per_day.get(f"{y:04d}-{m:02d}-{d:02d}", 0)
        cumulative.append(run)

    total_budget = sum(b for cid, b in budgets.items()
                       if conn.execute("SELECT kind FROM categories WHERE id=?", (cid,)).fetchone()["kind"] == "expense")

    q = ("SELECT CASE WHEN t.payee='' THEN t.booking_text ELSE t.payee END AS p, SUM(-t.amount_cents) s, COUNT(*) n "
         "FROM transactions t JOIN categories c ON c.id=t.category_id WHERE c.kind='expense' "
         "AND t.booking_date BETWEEN ? AND ?")
    args = [start, end]
    if account_id:
        q += " AND t.account_id=?"
        args.append(account_id)
    q += " GROUP BY p HAVING s > 0 ORDER BY s DESC LIMIT 8"
    top = [{"payee": r["p"], "spent_cents": r["s"], "count": r["n"]} for r in conn.execute(q, args)]

    projected_total = round(expenses / elapsed * days_in_month) if is_current and elapsed else expenses
    return {
        "month": month, "days_in_month": days_in_month, "elapsed_days": elapsed, "is_current": is_current,
        "income_cents": income, "expenses_cents": expenses, "net_cents": income - expenses,
        "savings_rate": round((income - expenses) / income, 4) if income > 0 else None,
        "transfers_cents": transfers_out,
        "projected_expenses_cents": projected_total, "total_budget_cents": total_budget,
        "categories": categories, "cumulative_cents": cumulative, "top_payees": top,
    }


def monthly_series(conn: sqlite3.Connection, months: int = 12, end_month: str | None = None,
                   account_id: int | None = None) -> list[dict]:
    end_month = end_month or date.today().strftime("%Y-%m")
    out = []
    for i in range(months - 1, -1, -1):
        mo = shift_month(end_month, -i)
        s, e = month_bounds(mo)
        inc = exp = 0
        for r in _spend_by_category(conn, s, e, account_id):
            if r["kind"] == "income":
                inc += r["s"]
            elif r["kind"] == "expense":
                exp += -r["s"]
        out.append({"month": mo, "income_cents": inc, "expenses_cents": exp, "net_cents": inc - exp})
    return out


def budget_suggestions(conn: sqlite3.Connection, month: str | None = None) -> list[dict]:
    """Vorschlag: Durchschnitt der letzten 3 vollen Monate, aufgerundet auf 10 €."""
    month = month or date.today().strftime("%Y-%m")
    tot: dict[int, float] = defaultdict(float)
    names: dict[int, str] = {}
    for i in (1, 2, 3):
        s, e = month_bounds(shift_month(month, -i))
        for r in _spend_by_category(conn, s, e):
            if r["kind"] == "expense" and r["s"] < 0:
                tot[r["id"]] += -r["s"] / 3
                names[r["id"]] = r["name"]
    out = []
    for cid, v in tot.items():
        cents = int(-(-v // 1000) * 1000)  # auf 10 € aufrunden
        if cents >= 1000:
            out.append({"category_id": cid, "name": names[cid], "suggested_cents": cents})
    out.sort(key=lambda x: -x["suggested_cents"])
    return out


# ---------------------------------------------------------------- Fixkosten / Abos

_INTERVALS = [  # (Name, min Tage, max Tage, Nominaltage, Mindestanzahl)
    ("monatlich", 26, 35, 30, 3),
    ("vierteljährlich", 84, 97, 91, 3),
    ("halbjährlich", 170, 195, 182, 2),
    ("jährlich", 350, 380, 365, 2),
]


def _payee_key(payee: str, booking_text: str) -> str:
    base = payee or booking_text
    base = re.sub(r"[^a-zäöüß ]", "", base.lower())
    return " ".join(base.split()[:3])


def detect_recurring(conn: sqlite3.Connection, today: date | None = None) -> list[dict]:
    today = today or date.today()
    rows = conn.execute(
        "SELECT t.booking_date d, t.amount_cents a, t.payee, t.booking_text, c.name cat, c.color, c.kind "
        "FROM transactions t LEFT JOIN categories c ON c.id=t.category_id "
        "WHERE c.kind IN ('expense','income') OR c.kind IS NULL ORDER BY t.booking_date"
    ).fetchall()
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if r["cat"] == "Bargeld":
            continue            # regelmäßige Abhebungen sind keine Verträge
        key = _payee_key(r["payee"], r["booking_text"])
        if len(key) < 3:
            continue
        groups[(key, 1 if r["a"] > 0 else -1)].append(r)

    result = []
    for (key, sign), items in groups.items():
        if len(items) < 2:
            continue
        dates = [date.fromisoformat(i["d"]) for i in items]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
        if not gaps:
            continue
        med_gap = statistics.median(gaps)
        match = next((iv for iv in _INTERVALS if iv[1] <= med_gap <= iv[2]), None)
        if not match:
            continue
        name, _lo, _hi, nominal, min_n = match
        # Nur Termine betrachten, die zum Intervall passen (Ausreißer ignorieren)
        regular = sum(1 for g in gaps if match[1] <= g <= match[2])
        if len(items) < min_n or regular / len(gaps) < 0.6:
            continue
        amounts = [abs(i["a"]) for i in items]
        med = statistics.median(amounts)
        stable = sum(1 for a in amounts if abs(a - med) <= 0.15 * med) / len(amounts)
        if stable < 0.6:
            continue
        last = dates[-1]
        next_exp = last + timedelta(days=nominal)
        # Monatlich: gleicher Tag im Folgemonat statt +30 Tage
        if name == "monatlich":
            y, m = last.year + (last.month // 12), last.month % 12 + 1
            next_exp = date(y, m, min(last.day, calendar.monthrange(y, m)[1]))
        active = (today - last).days <= nominal * 1.5
        latest = items[-1]
        result.append({
            "payee": latest["payee"] or latest["booking_text"] or key,
            "interval": name, "interval_days": nominal,
            "amount_cents": sign * round(med), "occurrences": len(items),
            "last_date": last.isoformat(), "next_expected": next_exp.isoformat(),
            "active": active, "category": latest["cat"], "color": latest["color"],
            "monthly_cents": sign * round(med * 30.4 / nominal),
        })
    result.sort(key=lambda x: (not x["active"], x["amount_cents"]))
    return result


def upcoming_this_month(conn: sqlite3.Connection, today: date | None = None) -> list[dict]:
    """Erwartete, noch nicht gebuchte wiederkehrende Ausgaben bis Monatsende."""
    today = today or date.today()
    end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    out = []
    for r in detect_recurring(conn, today):
        if not r["active"] or r["amount_cents"] >= 0:
            continue
        nxt = date.fromisoformat(r["next_expected"])
        if today < nxt <= end:
            out.append(r)
    out.sort(key=lambda r: r["next_expected"])
    return out
