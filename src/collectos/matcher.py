"""Bank-credit matcher: suggestions only.

Reads the credits on a bank statement and proposes which customer and invoices each one may
be for, with the reasons. It never changes a balance and never decides anything: your accounting
software stays the source of truth, and a person confirms every suggestion.

Rules, in order of trust:
  1. an invoice number in the narration            -> high
  2. the customer's name in the narration + an amount that equals their open invoices -> high
  3. the customer's name only                        -> medium
  4. an amount that equals exactly one open invoice  -> medium (low if it is a round number)
  5. part of a customer's name                        -> low
Amounts a few percent below the invoice are flagged as possible TDS (1, 2, 5 or 10 percent).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import combinations

from .dates import DateError, parse_date
from .models import Invoice, Promise, customer_key
from .money import MoneyError, format_inr, to_money
from .readers import RawRows

STOPWORDS = {"LTD", "LIMITED", "PVT", "PRIVATE", "CO", "COMPANY", "AND", "THE", "OF", "INC", "LLP", "CORP", "CORPORATION", "ENTERPRISES", "ENTERPRISE"}
TDS_RATES = (Decimal("0.01"), Decimal("0.02"), Decimal("0.05"), Decimal("0.10"))
TDS_TOLERANCE = Decimal("0.0015")
MAX_SUBSET = 4
MAX_CANDIDATES = 12

_ALIASES = {
    "date": ("date", "txndate", "transactiondate", "valuedate", "postingdate", "trandate"),
    "narration": ("narration", "description", "particulars", "transactiondetails", "remarks", "transactionremarks", "details"),
    "reference": ("chqrefno", "refno", "chequeno", "reference", "utr", "utrno", "refnochequeno", "chqno", "referenceno"),
    "credit": ("credit", "creditamount", "deposit", "depositamt", "depositamount", "cr", "amountcr", "creditamt"),
    "amount": ("amount", "transactionamount", "txnamount"),
    "kind": ("drcr", "crdr", "type", "transactiontype"),
}
_LOOKUP = {a: f for f, names in _ALIASES.items() for a in names}


class StatementError(ValueError):
    pass


@dataclass(frozen=True)
class Credit:
    row_no: int
    on: date | None
    narration: str
    amount: Decimal
    reference: str


@dataclass
class StatementReport:
    credits: list[Credit] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Suggestion:
    credit: Credit
    confidence: str  # high | medium | low | none
    customer: str | None
    invoice_nos: tuple[str, ...]
    matched_outstanding: Decimal | None
    reasons: tuple[str, ...]
    promise_ids: tuple[int, ...]


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower()) if value is not None else ""


def parse_statement(rows: RawRows) -> StatementReport:
    """Pull the credit lines out of a bank statement (CSV/XLSX rows). Debits are ignored."""
    header = None
    for idx, (_, cells) in enumerate(rows[:30]):
        cols: dict[str, int] = {}
        for i, cell in enumerate(cells):
            field_name = _LOOKUP.get(_norm(cell))
            if field_name and field_name not in cols:
                cols[field_name] = i
        if "narration" in cols and ("credit" in cols or ("amount" in cols and "kind" in cols)):
            header = (idx, cols)
            break
    if header is None:
        raise StatementError("could not find the statement columns. Need a narration/description column and a credit/deposit column "
                             "(or an amount column with a Dr/Cr column).")
    idx, cols = header
    report = StatementReport()

    def cell(cells, name):
        i = cols.get(name)
        return cells[i] if i is not None and i < len(cells) else None

    for row_no, cells in rows[idx + 1:]:
        if all(c is None or (isinstance(c, str) and not c.strip()) for c in cells):
            continue
        try:
            if "credit" in cols:
                raw = cell(cells, "credit")
                if raw is None or (isinstance(raw, str) and not raw.strip()):
                    report.skipped += 1
                    continue
                amount = to_money(repr(raw) if isinstance(raw, float) else raw)
            else:
                kind = str(cell(cells, "kind") or "").strip().lower()
                if not kind.startswith("c"):
                    report.skipped += 1
                    continue
                raw = cell(cells, "amount")
                amount = to_money(repr(raw) if isinstance(raw, float) else raw)
        except MoneyError as exc:
            report.errors.append(f"row {row_no}: {exc}")
            continue
        if amount <= 0:
            report.skipped += 1
            continue
        raw_date = cell(cells, "date")
        try:
            on = parse_date(raw_date) if raw_date not in (None, "") else None
        except DateError:
            on = None
        narration = re.sub(r"\s+", " ", str(cell(cells, "narration") or "")).strip()
        reference = str(cell(cells, "reference") or "").strip()
        report.credits.append(Credit(row_no, on, narration, amount, reference))
    return report


# ---- matching ------------------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Z0-9]+", text.upper())


def _joined(tokens: list[str]) -> set[str]:
    """Single tokens and runs of up to 3 adjacent tokens joined, so INV-1001 matches INV1001."""
    out = set(tokens)
    for n in (2, 3):
        for i in range(len(tokens) - n + 1):
            out.add("".join(tokens[i:i + n]))
    return out


def _name_tokens(name: str) -> set[str]:
    return {t for t in _tokens(name) if len(t) >= 3 and t not in STOPWORDS and not t.isdigit()}


def _token_hit(word: str, narration_tokens: list[str]) -> bool:
    """A name word appears in the narration, allowing for the truncation banks apply (min 5 letters)."""
    return any(t == word or (len(t) >= 5 and word.startswith(t)) or (len(word) >= 5 and t.startswith(word)) for t in narration_tokens)


def _name_score(sig: set[str], narration_tokens: list[str]) -> Decimal:
    if not sig:
        return Decimal(0)
    return Decimal(sum(1 for word in sig if _token_hit(word, narration_tokens))) / Decimal(len(sig))


def _best_subset(amount: Decimal, invoices: list[Invoice]) -> tuple[str, list[Invoice], Decimal] | None:
    """Exact match first, then a TDS-adjusted match. Smallest subsets first, oldest invoices first."""
    pool = sorted(invoices, key=lambda i: (i.due_date or i.invoice_date, i.invoice_no))[:MAX_CANDIDATES]
    tds_hit = None
    for size in range(1, min(MAX_SUBSET, len(pool)) + 1):
        for combo in combinations(pool, size):
            total = sum((i.outstanding for i in combo), Decimal("0.00"))
            if total == amount:
                return "exact", list(combo), total
            if tds_hit is None and total > amount:
                shortfall = 1 - amount / total
                for rate in TDS_RATES:
                    if abs(shortfall - rate) <= TDS_TOLERANCE:
                        tds_hit = ("tds", list(combo), total)
    return tds_hit


def suggest(credits: Iterable[Credit], invoices: Iterable[Invoice], as_of: date, open_promises: Iterable[Promise] = ()) -> list[Suggestion]:
    open_inv = [i for i in invoices if i.outstanding > 0]
    by_cust: dict[str, list[Invoice]] = {}
    for i in open_inv:
        by_cust.setdefault(customer_key(i.customer), []).append(i)
    names = {k: v[0].customer for k, v in by_cust.items()}
    sigs = {k: _name_tokens(n) for k, n in names.items()}
    freq: dict[str, int] = {}
    for sig in sigs.values():
        for word in sig:
            freq[word] = freq.get(word, 0) + 1
    promises: dict[str, list[int]] = {}
    for p in open_promises:
        promises.setdefault(p.customer_key, []).append(p.id)
    compact_nos = {i: re.sub(r"[^A-Z0-9]", "", i.invoice_no.upper()) for i in open_inv}

    out = []
    for credit in credits:
        toks = _tokens(credit.narration)
        joined = _joined(toks)
        reasons: list[str] = []

        hits = [i for i in open_inv if len(compact_nos[i]) >= 4 and compact_nos[i] in joined]
        scores = {k: _name_score(sig, toks) for k, sig in sigs.items() if sig}
        full = [k for k, sc in scores.items() if sc == 1]
        partial = [k for k, sc in scores.items() if Decimal("0.5") <= sc < 1]

        cust_key: str | None = None
        pool: list[Invoice] = []
        confidence = "none"

        hit_customers = {customer_key(i.customer) for i in hits}
        if len(hit_customers) == 1:
            cust_key, pool, confidence = next(iter(hit_customers)), hits, "high"
            reasons.append("invoice number " + ", ".join(i.invoice_no for i in hits) + " appears in the narration")
        elif len(hit_customers) > 1:
            narrowed = [k for k in hit_customers if k in full]
            if len(narrowed) == 1:
                cust_key, confidence = narrowed[0], "high"
                pool = [i for i in hits if customer_key(i.customer) == cust_key]
                reasons.append("invoice number " + ", ".join(i.invoice_no for i in pool) + " and the customer name appear in the narration")
            else:
                confidence = "low"
                reasons.append("invoice numbers from more than one customer appear in the narration")
        elif len(full) == 1:
            cust_key, pool, confidence = full[0], by_cust[full[0]], "medium"
            reasons.append("the customer's name appears in the narration")
        elif len(full) > 1:
            confidence = "low"
            reasons.append("more than one customer's name matches: " + ", ".join(sorted(names[k] for k in full)))
        elif partial:
            best = max(scores[k] for k in partial)
            tops = [k for k in partial if scores[k] == best]
            if len(tops) == 1:
                cust_key, pool, confidence = tops[0], by_cust[tops[0]], "low"
                reasons.append("part of the customer's name appears in the narration")
            else:
                confidence = "low"
                reasons.append("part of more than one customer's name appears in the narration")
        else:
            # a word of 4+ letters that belongs to exactly one customer's name is a weak clue on its own
            distinct = {k: [w for w in sorted(sig) if freq[w] == 1 and len(w) >= 4 and _token_hit(w, toks)] for k, sig in sigs.items()}
            distinct = {k: v for k, v in distinct.items() if v}
            if len(distinct) == 1:
                cust_key = next(iter(distinct))
                pool, confidence = by_cust[cust_key], "low"
                reasons.append(f"the word {distinct[cust_key][0]!r} in the narration appears only in this customer's name")
            elif len(distinct) > 1:
                confidence = "low"
                reasons.append("distinctive words from more than one customer's name appear in the narration")

        chosen: list[Invoice] = []
        matched_total: Decimal | None = None
        if cust_key is not None:
            found = _best_subset(credit.amount, pool)
            if found is None and pool is not by_cust[cust_key]:
                found = _best_subset(credit.amount, by_cust[cust_key])
            if found is not None:
                kind, chosen, matched_total = found
                labels = " + ".join(i.invoice_no for i in chosen)
                if kind == "exact":
                    reasons.append(f"the amount equals the outstanding on {labels}")
                    if confidence == "medium":
                        confidence = "high"
                else:
                    pct = (1 - credit.amount / matched_total) * 100
                    reasons.append(f"the amount is about {pct:.0f}% below the outstanding on {labels} ({format_inr(matched_total)}): "
                                   "could be TDS deducted by the customer; confirm with their remittance advice")
            else:
                outstanding_all = sum((i.outstanding for i in by_cust[cust_key]), Decimal("0.00"))
                if credit.amount > outstanding_all:
                    reasons.append(f"the amount is more than everything this customer owes ({format_inr(outstanding_all)}); it may include an advance")
                elif hits:
                    chosen = pool
                    matched_total = sum((i.outstanding for i in pool), Decimal("0.00"))
                    diff = matched_total - credit.amount
                    reasons.append(f"the amount differs from the outstanding on these invoices ({format_inr(matched_total)}) by {format_inr(abs(diff))}")
                else:
                    reasons.append("the amount does not equal any combination of their open invoices")
        else:
            same = [i for i in open_inv if i.outstanding == credit.amount]
            if len(same) == 1:
                cust_key, chosen = customer_key(same[0].customer), same
                matched_total = same[0].outstanding
                confidence = "low" if credit.amount % 1000 == 0 else "medium"
                reasons.append(f"the amount equals the outstanding on {same[0].invoice_no} and no other open invoice")
            elif len(same) > 1:
                reasons.append(f"the amount equals the outstanding on {len(same)} different invoices, so it cannot identify one")
            if not reasons:
                reasons.append("nothing in the narration or amount points to a customer")

        out.append(Suggestion(credit, confidence, names.get(cust_key) if cust_key else None,
                              tuple(i.invoice_no for i in chosen), matched_total, tuple(reasons),
                              tuple(promises.get(cust_key, ())) if cust_key else ()))
    return out
