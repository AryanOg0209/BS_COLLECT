"""Optional AI assist. Off by default, and never required.

What it does (and why it is safe to switch on):
  * Reply reader: you paste what a customer wrote ("Friday tak kar dunga, 50 hazaar") and it suggests
    the promise date, amount and kind of reply. Only that text is sent, after phone numbers, emails,
    PAN/GSTIN and long account numbers are masked. A person confirms before anything is saved.
  * Column helper: for an export CollectOS does not recognise, it suggests which column is which. Only
    the title rows are sent; every data cell is replaced by its type (<number>, <date>, <text:12>), so
    no customer name or amount leaves this computer.
  * Remittance reader (see read_remittance): a payer's remittance advice (PDF or photo) is read into
    invoice numbers, amounts and TDS. One document, and only when you choose to send it.

Guard rails: switched on per installation in Settings; the key is read only from the environment
(ANTHROPIC_API_KEY); every call is recorded with the exact text sent; a monthly spend cap stops further
calls; the model's answer is untrusted and is re-validated here (dates, amounts, column numbers, and a
requirement that a promise is backed by a verbatim quote from the message).
"""

import base64
import importlib.util
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from .dates import DateError, parse_date
from .importer import ColumnMap
from .money import MoneyError, to_money
from .readers import RawRows

DEFAULT_MODEL = "claude-haiku-5-5"
# USD per million tokens (input, output). Cached 2026-10-06; ESTIMATES used only for the spending cap.
# $/MTok equals micro-dollars per token, so cost_micro_usd = input_tokens * price_in + output_tokens * price_out.
PRICES = {
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
    "claude-fable-5-1": (Decimal("10.00"), Decimal("50.00")),
}
UNKNOWN_MODEL_PRICE = (Decimal("10.00"), Decimal("50.00"))   # assume the most expensive when unsure
MAX_REPLY_CHARS = 2000
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
FIELDS = ("invoice_no", "customer", "invoice_date", "due_date", "amount", "paid", "outstanding")
INTENTS = ("promise_to_pay", "already_paid", "dispute", "needs_more_time", "refuses", "wrong_person", "unclear")
DISPUTE_CATEGORIES = ("none", "price", "quantity", "quality", "delivery", "tax", "duplicate", "other")
CONFIDENCE = ("high", "medium", "low")


class AiError(RuntimeError):
    """Something went wrong with an AI request (the message is safe to show to the user)."""


class AiInputError(ValueError):
    """The request itself was unusable (empty paste, wrong file type). A user mistake, not an AI failure."""


class AiUnavailable(AiError):
    """AI assist cannot run right now: switched off, no key, not installed, or over budget."""


class AiRefused(AiError):
    """The model declined the request."""


class AiBadOutput(AiError):
    """The model answered, but not in a form we can trust."""


# ---- cost ---------------------------------------------------------------------------------------------------------


def estimate_cost_micro_usd(model: str, input_tokens: int, output_tokens: int) -> int:
    price_in, price_out = PRICES.get(model, UNKNOWN_MODEL_PRICE)
    return math.ceil(Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out)


def usd_to_inr(micro_usd: int, rate: str) -> Decimal:
    return (Decimal(micro_usd) / Decimal(1_000_000) * Decimal(rate)).quantize(Decimal("0.01"))


# ---- redaction ------------------------------------------------------------------------------------------------------

_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_UPI = re.compile(r"(?<![\w.])[\w.\-]{2,}@[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:(?:\+|00)?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_LONG_NUMBER = re.compile(r"(?<![\d,])\d{9,}(?!\d)")


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    """Mask identifiers that a message does not need to send. Amounts, dates and words are left alone."""
    counts: dict[str, int] = {}

    def sub(pattern, label, value):
        new, n = pattern.subn(f"[{label}]", value)
        if n:
            counts[label] = counts.get(label, 0) + n
        return new

    text = sub(_GSTIN, "gstin", text)
    text = sub(_PAN, "pan", text)
    text = sub(_EMAIL, "email", text)
    text = sub(_UPI, "upi", text)
    text = sub(_PHONE, "phone", text)
    text = sub(_LONG_NUMBER, "number", text)
    return text, counts


# ---- masking a file layout for the column helper ---------------------------------------------------------------------


def _kind(value) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "blank"
    if isinstance(value, (datetime, date)):
        return "date"
    if isinstance(value, bool):
        return "text"
    if isinstance(value, (int, float)):
        return "number"
    text = str(value).strip()
    try:
        parse_date(text)
        return "date"
    except DateError:
        pass
    try:
        to_money(text)
        return "number"
    except MoneyError:
        return "text"


def mask_rows(rows: RawRows, max_rows: int = 40, max_cols: int = 15) -> str:
    """Describe the top of a sheet without sending any data. Title and header text is kept; once the
    first row that contains a number or a date appears, every later cell is replaced by its type."""
    lines, data_started = [], False
    for row_no, cells in rows[:max_rows]:
        cells = list(cells)[:max_cols]
        kinds = [_kind(c) for c in cells]
        if not data_started and any(k in ("number", "date") for k in kinds):
            data_started = True
        shown = []
        for cell, kind in zip(cells, kinds):
            if kind == "blank":
                shown.append("")
            elif data_started:
                shown.append(f"<text:{min(len(str(cell).strip()), 99)}>" if kind == "text" else f"<{kind}>")
            else:
                shown.append(redact_text(str(cell).strip()[:40])[0])
        lines.append(f"row {row_no}: | " + " | ".join(shown) + " |")
    return "\n".join(lines)


def pick_sheet(sheets) -> tuple[str | None, RawRows]:
    """The first sheet that looks like a table (several rows with two or more filled cells)."""
    for name, rows in sheets:
        filled = sum(1 for _, cells in rows if sum(1 for c in cells if _kind(c) != "blank") >= 2)
        if filled >= 3:
            return name, rows
    if not sheets:
        raise AiBadOutput("the file has no sheets")
    return sheets[0]


def validate_column_map(data: dict, rows: RawRows, sheet: str | None) -> ColumnMap:
    """Turn a suggested mapping (1-based columns, 0 = absent) into a checked ColumnMap (0-based)."""
    header_row, columns = data.get("header_row"), data.get("columns")
    if not isinstance(header_row, int) or isinstance(header_row, bool) or not isinstance(columns, dict):
        raise AiBadOutput("the suggested mapping is malformed")
    if header_row not in {n for n, _ in rows}:
        raise AiBadOutput(f"the suggested header row {header_row} is not in the file")
    width = max((len(c) for _, c in rows), default=0)
    mapped: dict[str, int] = {}
    for name in FIELDS:
        col = columns.get(name, 0)
        if not isinstance(col, int) or isinstance(col, bool) or not 0 <= col <= width:
            raise AiBadOutput(f"the suggested column for {name} is not a column in the file")
        if col:
            mapped[name] = col - 1
    if len(set(mapped.values())) != len(mapped):
        raise AiBadOutput("two fields were mapped to the same column")
    missing = [f for f in ("invoice_no", "customer", "invoice_date") if f not in mapped]
    if "amount" not in mapped and "outstanding" not in mapped:
        missing.append("amount or outstanding")
    if missing:
        raise AiBadOutput("the suggestion does not cover: " + ", ".join(missing))
    return ColumnMap(sheet=sheet, header_row=header_row, columns=mapped)


# ---- prompts and schemas -------------------------------------------------------------------------------------------------

REPLY_SYSTEM = """You help a small Indian business record what a customer said when asked to pay an overdue invoice.
The message may be in English, Hindi, Hinglish (Hindi in Roman letters) or another Indian language.
Read ONLY the message. Do not invent anything that is not in it.

Rules:
- intent: promise_to_pay (names or clearly implies a payment day or says it will be paid), already_paid, dispute (says the invoice or amount is wrong), needs_more_time (asks for time without a day), refuses, wrong_person, or unclear.
- promise_date: only when the customer states or clearly implies a specific day ("Friday", "kal", "parso", "next Monday", "15th", "by month end"). Work it out from today's date given below. Output YYYY-MM-DD. "This week", "soon" or "jaldi" give no specific day: return an empty string.
- promise_amount: only when an amount is stated. Digits only, no currency sign or commas, e.g. 50000. Understand "hazaar/thousand", "lakh" (100000) and "crore" (10000000). Otherwise an empty string.
- dispute_category: if intent is dispute, the closest of price, quantity, quality, delivery, tax, duplicate, other; otherwise none.
- quote: the exact words from the message that support the intent, date or amount, copied character for character. An empty string if none.
- summary: at most 120 characters of plain English for a record of the call, e.g. "Said he will pay Rs 50,000 on Friday".
- confidence: high only when the message is explicit; low when you are guessing."""

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "promise_date": {"type": "string"},
        "promise_amount": {"type": "string"},
        "dispute_category": {"type": "string", "enum": list(DISPUTE_CATEGORIES)},
        "quote": {"type": "string"},
        "summary": {"type": "string"},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
    },
    "required": ["intent", "promise_date", "promise_amount", "dispute_category", "quote", "summary", "confidence"],
    "additionalProperties": False,
}

COLUMNS_SYSTEM = """You help map the columns of an accounting export (Tally, Zoho, Busy or a spreadsheet) for a receivables tool.
You are shown the top rows of one sheet. Text in the title and header rows is real. Once data starts, every cell is replaced by its type: <number>, <date> or <text:N> (N = length), so you never see actual data.

Identify the header row (the row number exactly as shown) and, for each field, the column number (counting from 1, left to right) that holds it:
- invoice_no: the bill, invoice, reference or voucher number
- customer: the party, ledger or debtor name
- invoice_date: the bill or invoice date
- due_date: the due date
- amount: the original invoice amount
- paid: the amount already received against the invoice
- outstanding: the pending or balance amount
Use 0 for any field that is not clearly present. Never guess. Notes: one short sentence saying anything the user should check."""

COLUMNS_SCHEMA = {
    "type": "object",
    "properties": {
        "header_row": {"type": "integer"},
        "columns": {
            "type": "object",
            "properties": {f: {"type": "integer"} for f in FIELDS},
            "required": list(FIELDS),
            "additionalProperties": False,
        },
        "notes": {"type": "string"},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
    },
    "required": ["header_row", "columns", "notes", "confidence"],
    "additionalProperties": False,
}

REMITTANCE_SYSTEM = """You read a customer's remittance advice or payment confirmation (PDF or photo) for a small Indian supplier.
Extract only what is printed on the document. Never calculate, guess or fill gaps.
- payer: the name of the business that paid.
- payment_date: YYYY-MM-DD, or an empty string if not shown.
- reference: the UTR / cheque / transaction reference, or an empty string.
- total_received: the net amount actually paid, digits only (no currency sign or commas), or an empty string.
- total_tds: the total tax deducted at source, digits only, or an empty string.
- invoices: each invoice the payment is for, with its number and the gross amount, TDS and net amount shown for that invoice (digits only, empty string if not shown).
- notes: one short sentence about anything unclear (blurred text, missing totals). Say "none" if all is clear.
- confidence: high only if the text is clear and the totals are printed."""

REMITTANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "payer": {"type": "string"},
        "payment_date": {"type": "string"},
        "reference": {"type": "string"},
        "total_received": {"type": "string"},
        "total_tds": {"type": "string"},
        "invoices": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"invoice_no": {"type": "string"}, "gross": {"type": "string"}, "tds": {"type": "string"}, "net": {"type": "string"}},
                "required": ["invoice_no", "gross", "tds", "net"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
    },
    "required": ["payer", "payment_date", "reference", "total_received", "total_tds", "invoices", "notes", "confidence"],
    "additionalProperties": False,
}


def _check_schema(value, schema, path="answer"):
    """A small validator for the schema subset used above. The API enforces the schema, but the answer is
    untrusted input, so it is checked again here."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise AiBadOutput(f"{path} is not an object")
        props = schema["properties"]
        for key in schema.get("required", []):
            if key not in value:
                raise AiBadOutput(f"{path} is missing {key}")
        extra = set(value) - set(props)
        if extra:
            raise AiBadOutput(f"{path} has unexpected fields: {', '.join(sorted(extra))}")
        for key, sub in props.items():
            _check_schema(value[key], sub, f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list):
            raise AiBadOutput(f"{path} is not a list")
        if len(value) > 200:
            raise AiBadOutput(f"{path} is unreasonably long")
        for i, item in enumerate(value):
            _check_schema(item, schema["items"], f"{path}[{i}]")
    elif kind == "string":
        if not isinstance(value, str):
            raise AiBadOutput(f"{path} is not text")
        if "enum" in schema and value not in schema["enum"]:
            raise AiBadOutput(f"{path} has an unexpected value")
        if len(value) > 4000:
            raise AiBadOutput(f"{path} is too long")
    elif kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise AiBadOutput(f"{path} is not a whole number")


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


# ---- the assistant -----------------------------------------------------------------------------------------------------------


class Assistant:
    """Wraps the Claude API behind the guard rails. `client` can be injected (tests use a fake)."""

    def __init__(self, store, actor: str = "dashboard", client=None, model: str | None = None, environ=None):
        self.store = store
        self.actor = actor
        self._client = client
        self.env = os.environ if environ is None else environ
        self.model = model or self.env.get("COLLECTOS_AI_MODEL") or DEFAULT_MODEL

    # -- status and gating ----------------------------------------------------------------------------------------
    @property
    def key_present(self) -> bool:
        return self._client is not None or bool(self.env.get("ANTHROPIC_API_KEY") or self.env.get("ANTHROPIC_AUTH_TOKEN"))

    @property
    def sdk_present(self) -> bool:
        return self._client is not None or importlib.util.find_spec("anthropic") is not None

    def status(self) -> dict:
        month = self.store.ai_month_summary()
        rate = self.store.get_setting("ai_usd_inr")
        budget = Decimal(self.store.get_setting("ai_budget_usd"))
        return {
            "enabled": self.store.get_setting("ai_assist") == "1", "key_present": self.key_present, "sdk_present": self.sdk_present,
            "model": self.model, "month": month["month"], "calls": month["calls"],
            "cost_usd": f"{Decimal(month['cost_micro_usd']) / Decimal(1_000_000):.4f}",
            "cost_inr": f"{usd_to_inr(month['cost_micro_usd'], rate):.2f}",
            "budget_usd": f"{budget:.2f}", "budget_inr": f"{(budget * Decimal(rate)).quantize(Decimal('0.01')):.2f}", "usd_inr": rate,
            "model_known": self.model in PRICES,
        }

    def _gate(self) -> None:
        if self.store.get_setting("ai_assist") != "1":
            raise AiUnavailable("AI assist is switched off. Turn it on in Settings first.")
        if not self.sdk_present:
            raise AiUnavailable("The AI add-on is not installed. Run: pip install 'collectos[ai]'")
        if not self.key_present:
            raise AiUnavailable("No API key found. Put ANTHROPIC_API_KEY=... in the .env file or your environment, then restart.")
        used = self.store.ai_month_summary()["cost_micro_usd"]
        if Decimal(used) >= Decimal(self.store.get_setting("ai_budget_usd")) * Decimal(1_000_000):
            raise AiUnavailable("This month's AI spending cap has been reached. Raise it in Settings if you want to continue.")

    def _get_client(self):
        if self._client is None:
            import anthropic  # imported here so the rest of CollectOS works without the package

            self._client = anthropic.Anthropic()
        return self._client

    # -- one guarded call ---------------------------------------------------------------------------------------------------
    def _complete(self, feature: str, system: str, content, schema: dict, sent_text: str, max_tokens: int = 1024) -> dict:
        self._gate()
        client = self._get_client()
        try:
            response = client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            )
        except Exception as exc:  # translated into messages that are safe to show
            raise self._translate(exc) from None
        usage = getattr(response, "usage", None)
        tin, tout = int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)
        self.store.record_ai_usage(feature, self.model, tin, tout, estimate_cost_micro_usd(self.model, tin, tout), self.actor, sent_text)
        if getattr(response, "stop_reason", None) == "refusal":
            raise AiRefused("The AI declined to read this. Enter the details by hand.")
        if getattr(response, "stop_reason", None) == "max_tokens":
            raise AiBadOutput("The AI's answer was cut off. Try again, or enter the details by hand.")
        text = next((b.text for b in getattr(response, "content", []) if getattr(b, "type", None) == "text"), None)
        if text is None:
            raise AiBadOutput("The AI returned no answer.")
        try:
            data = json.loads(text)
        except ValueError:
            raise AiBadOutput("The AI's answer was not valid JSON.") from None
        _check_schema(data, schema)
        return data

    @staticmethod
    def _translate(exc: Exception) -> AiError:
        name = type(exc).__name__
        if name in ("AuthenticationError", "PermissionDeniedError"):
            return AiUnavailable("The API key was rejected. Check ANTHROPIC_API_KEY (it may be revoked or mistyped).")
        if name == "RateLimitError":
            return AiError("The AI service is busy right now. Try again in a minute.")
        if name in ("APIConnectionError", "APITimeoutError"):
            return AiError("Could not reach the AI service. Check the internet connection.")
        if name == "NotFoundError":
            return AiUnavailable("That model is not available to this key. Change COLLECTOS_AI_MODEL.")
        if name == "BadRequestError":
            return AiError("The AI service could not process that request.")
        return AiError(f"The AI request failed ({name}).")

    # -- feature 1: reading a customer's reply ----------------------------------------------------------------------------------
    def read_reply(self, text: str, as_of: date) -> dict:
        cleaned = (text or "").strip()
        if not cleaned:
            raise AiInputError("Paste the customer's reply first.")
        if len(cleaned) > MAX_REPLY_CHARS:
            raise AiInputError(f"That is longer than {MAX_REPLY_CHARS} characters. Paste just the reply.")
        redacted, counts = redact_text(cleaned)
        user = f"Today is {as_of:%A}, {as_of.isoformat()}.\n\nCustomer's message:\n<<<\n{redacted}\n>>>"
        data = self._complete("reply_reader", REPLY_SYSTEM, user, REPLY_SCHEMA, sent_text=user, max_tokens=600)
        result = self._check_reply(data, redacted, as_of)
        result["masked"] = counts
        return result

    @staticmethod
    def _check_reply(data: dict, message: str, as_of: date) -> dict:
        warnings: list[str] = []
        confidence = data["confidence"]
        quote = data["quote"].strip()
        supported = bool(quote) and _normalise(quote) in _normalise(message)
        promise_date = promise_amount = None

        raw_date = data["promise_date"].strip()
        if raw_date:
            try:
                parsed = parse_date(raw_date)
                if not as_of <= parsed <= as_of + timedelta(days=400):
                    warnings.append(f"The suggested date {parsed.isoformat()} is in the past or too far ahead, so it was ignored.")
                else:
                    promise_date = parsed.isoformat()
            except DateError:
                warnings.append("The suggested date was not a valid date, so it was ignored.")
        raw_amount = re.sub(r"[,\s₹]", "", data["promise_amount"])
        if raw_amount:
            try:
                amount = to_money(raw_amount)
                if amount <= 0 or amount > Decimal("10000000000"):
                    raise MoneyError("out of range")
                promise_amount = f"{amount:.2f}"
            except MoneyError:
                warnings.append("The suggested amount was not a valid amount, so it was ignored.")
        if (promise_date or promise_amount) and not supported:
            promise_date = promise_amount = None
            confidence = "low"
            warnings.append("The AI could not point to the words that show this, so the date and amount were left out. Check the message yourself.")
        dispute = None if data["dispute_category"] == "none" else data["dispute_category"]
        if data["intent"] == "dispute" and dispute is None:
            dispute = "other"
        summary = re.sub(r"\s+", " ", data["summary"]).strip()[:160] or "Customer replied (see the note)."
        return {"intent": data["intent"], "promise_date": promise_date, "promise_amount": promise_amount, "dispute_category": dispute,
                "quote": quote if supported else "", "summary": summary, "confidence": confidence, "evidence_found": supported, "warnings": warnings}

    # -- feature 2: suggesting which column is which ----------------------------------------------------------------------------------
    def map_columns(self, sheets) -> dict:
        name, rows = pick_sheet(sheets)
        masked = mask_rows(rows)
        user = "Top rows of the sheet:\n" + masked
        data = self._complete("column_helper", COLUMNS_SYSTEM, user, COLUMNS_SCHEMA, sent_text=user, max_tokens=700)
        column_map = validate_column_map(data, rows, name)
        return {"sheet": name, "header_row": column_map.header_row, "columns": {f: c + 1 for f, c in column_map.columns.items()},
                "notes": data["notes"].strip()[:300], "confidence": data["confidence"]}

    # -- feature 3: reading a remittance advice (PDF or photo) -------------------------------------------------------------------------
    def read_remittance(self, data: bytes, media_type: str) -> dict:
        if len(data) > MAX_DOCUMENT_BYTES:
            raise AiInputError("That file is larger than 5 MB.")
        if media_type == "application/pdf":
            block = {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode()}}
        elif media_type in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            block = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode()}}
        else:
            raise AiInputError("Use a PDF or a PNG/JPEG photo.")
        content = [block, {"type": "text", "text": "Read this remittance advice."}]
        result = self._complete("remittance_reader", REMITTANCE_SYSTEM, content, REMITTANCE_SCHEMA,
                                sent_text=f"[{media_type} document, {len(data)} bytes sent to the AI]", max_tokens=1500)
        return self._check_remittance(result)

    @staticmethod
    def _amount(raw: str, warnings: list[str], what: str) -> str | None:
        cleaned = re.sub(r"[,\s₹]", "", raw)
        if not cleaned:
            return None
        try:
            amount = to_money(cleaned)
            if amount < 0 or amount > Decimal("10000000000"):
                raise MoneyError("range")
            return f"{amount:.2f}"
        except MoneyError:
            warnings.append(f"{what} was not a valid amount and was ignored.")
            return None

    def _check_remittance(self, data: dict) -> dict:
        warnings: list[str] = []
        payment_date = None
        if data["payment_date"].strip():
            try:
                payment_date = parse_date(data["payment_date"].strip()).isoformat()
            except DateError:
                warnings.append("The payment date was not valid and was ignored.")
        invoices = []
        for item in data["invoices"]:
            number = re.sub(r"\s+", " ", item["invoice_no"]).strip()[:60]
            if not number:
                continue
            invoices.append({"invoice_no": number, "gross": self._amount(item["gross"], warnings, f"Gross for {number}"),
                             "tds": self._amount(item["tds"], warnings, f"TDS for {number}"), "net": self._amount(item["net"], warnings, f"Net for {number}")})
        total_received = self._amount(data["total_received"], warnings, "The total received")
        total_tds = self._amount(data["total_tds"], warnings, "The total TDS")
        # cross-check the printed totals against the lines; a mismatch is flagged, never silently fixed
        nets = [Decimal(i["net"]) for i in invoices if i["net"]]
        if total_received and nets and len(nets) == len(invoices) and sum(nets) != Decimal(total_received):
            warnings.append("The invoice lines do not add up to the total received. Check the document.")
        confidence = data["confidence"] if not warnings else "low"
        return {"payer": re.sub(r"\s+", " ", data["payer"]).strip()[:120], "payment_date": payment_date, "reference": data["reference"].strip()[:60],
                "total_received": total_received, "total_tds": total_tds, "invoices": invoices, "notes": data["notes"].strip()[:300],
                "confidence": confidence, "warnings": warnings}


def check_connection(client, model: str) -> dict:
    """One tiny request for `collectos ai-check`: proves the key, the model and the JSON schema all work."""
    user = "Today is Monday, 2026-10-05.\n\nCustomer's message:\n<<<\nBhai Friday tak 50 hazaar bhej dunga\n>>>"
    response = client.messages.create(
        model=model, max_tokens=600, system=REPLY_SYSTEM, messages=[{"role": "user", "content": user}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": REPLY_SCHEMA}},
    )
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    _check_schema(data, REPLY_SCHEMA)
    tin, tout = int(response.usage.input_tokens), int(response.usage.output_tokens)
    return {"answer": data, "input_tokens": tin, "output_tokens": tout, "cost_micro_usd": estimate_cost_micro_usd(model, tin, tout)}
