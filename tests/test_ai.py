import base64
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from collectos import ai
from collectos.ai import (
    Assistant,
    AiBadOutput,
    AiError,
    AiInputError,
    AiRefused,
    AiUnavailable,
    estimate_cost_micro_usd,
    mask_rows,
    redact_text,
    usd_to_inr,
    validate_column_map,
)
from collectos.importer import ColumnMap, ImportFileError, NoInvoiceTable, import_sheets
from collectos.store import Store

MON = date(2026, 10, 5)      # a Monday
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)


class FakeClient:
    """Stands in for anthropic.Anthropic(). Records every request; returns queued answers or raises queued errors."""

    def __init__(self, *answers, tin=300, tout=60, stop_reason="end_turn"):
        self.answers, self.calls, self.messages = list(answers), [], self
        self.tin, self.tout, self.stop_reason = tin, tout, stop_reason

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], usage=SimpleNamespace(input_tokens=self.tin, output_tokens=self.tout),
                               stop_reason=self.stop_reason)


def reply(**over):
    base = {"intent": "promise_to_pay", "promise_date": "2026-10-09", "promise_amount": "50000", "dispute_category": "none",
            "quote": "Friday tak 50 hazaar", "summary": "Said he will pay Rs 50,000 on Friday", "confidence": "high"}
    base.update(over)
    return base


@pytest.fixture
def store():
    with Store(now=lambda: NOW) as s:
        s.set_setting("ai_assist", "1", "t")
        yield s


def assistant(store, *answers, **kw):
    client = FakeClient(*answers, **kw.pop("client_kw", {}))
    return Assistant(store, actor="priya", client=client, **kw), client


# ---- redaction -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("call me on 98765 43210 please", "call me on [phone] please"),
    ("+91-9876543210", "[phone]"), ("0091 98765-43210", "[phone]"),
    ("gst 27AAPFU0939F1ZV ok", "gst [gstin] ok"), ("pan ABCDE1234F", "pan [pan]"),
    ("mail a.b@acme.co.in now", "mail [email] now"), ("pay to rahul@okhdfcbank", "pay to [upi]"),
    ("a/c 123456789012 utr 987654321098", "a/c [number] utr [number]"),
])
def test_redaction_masks_identifiers(text, expected):
    assert redact_text(text)[0] == expected


@pytest.mark.parametrize("text", ["Friday tak 50,000 bhej dunga", "₹5000 on 15/10/2026", "2 lakh by month end", "invoice INV-1042 for 1,23,456.50", "kal tak, 50 hazaar"])
def test_redaction_leaves_what_the_reader_needs(text):
    assert redact_text(text)[0] == text


def test_redaction_counts():
    _, counts = redact_text("98765 43210 and a@b.co and 98765 43211")
    assert counts == {"email": 1, "phone": 2}


# ---- cost ---------------------------------------------------------------------------------------------------------------


def test_cost_estimates_use_the_price_table_and_assume_worst_case_for_unknown_models():
    assert estimate_cost_micro_usd("claude-haiku-5-5", 1_000_000, 0) == 100_000          # $0.10 per million input tokens
    assert estimate_cost_micro_usd("claude-haiku-5-5", 400, 80) == 40 + 40               # 400*0.10 + 80*0.50 micro-dollars
    assert estimate_cost_micro_usd("claude-opus-5-5", 1000, 100) == 4000 + 2000
    assert estimate_cost_micro_usd("some-future-model", 1000, 100) == 10_000 + 5_000     # priced as the dearest
    assert usd_to_inr(1_000_000, "90.00") == Decimal("90.00")


# ---- gating -----------------------------------------------------------------------------------------------------------------


def test_off_by_default_and_nothing_is_called(store):
    store.set_setting("ai_assist", "0", "t")
    a, client = assistant(store, reply())
    with pytest.raises(AiUnavailable, match="switched off"):
        a.read_reply("Friday", MON)
    assert client.calls == []


def test_no_key_means_unavailable_without_calling_anything(store):
    a = Assistant(store, environ={})                       # no injected client, no key
    a.sdk_present  # the property must not raise
    with pytest.raises(AiUnavailable):
        a.read_reply("Friday", MON)
    assert a.status()["key_present"] is False


def test_the_key_is_read_only_from_the_environment(store):
    assert Assistant(store, environ={"ANTHROPIC_API_KEY": "k"}).key_present is True
    assert Assistant(store, environ={"ANTHROPIC_AUTH_TOKEN": "t"}).key_present is True


def test_spend_cap_stops_further_calls(store):
    store.set_setting("ai_budget_usd", "0.01", "t")
    store.record_ai_usage("reply_reader", "claude-haiku-5-5", 1, 1, 10_000, "a", "x")       # exactly the cap
    a, client = assistant(store, reply())
    with pytest.raises(AiUnavailable, match="spending cap"):
        a.read_reply("Friday", MON)
    assert client.calls == []


def test_cap_is_per_month(store):
    store.set_setting("ai_budget_usd", "0.01", "t")
    store.record_ai_usage("reply_reader", "m", 1, 1, 10_000, "a", "x")
    next_month = datetime(2026, 11, 2, tzinfo=timezone.utc)
    store._now = lambda: next_month
    a, client = assistant(store, reply(promise_date="2026-11-05"))
    assert a.read_reply("Friday tak 50 hazaar", date(2026, 11, 2))["promise_date"] == "2026-11-05"


def test_status_reports_money_in_rupees(store):
    store.record_ai_usage("reply_reader", "claude-haiku-5-5", 400, 80, 2_000_000, "a", "x")     # $2.00
    s = Assistant(store, client=FakeClient()).status()
    assert (s["enabled"], s["calls"], s["cost_usd"], s["cost_inr"], s["budget_usd"], s["budget_inr"]) == (True, 1, "2.0000", "180.00", "2.00", "180.00")
    assert s["model"] == "claude-haiku-5-5" and s["model_known"] is True


# ---- the request itself ---------------------------------------------------------------------------------------------------------


def test_request_shape_follows_the_api_rules(store):
    a, client = assistant(store, reply())
    a.read_reply("Bhai Friday tak 50 hazaar bhej dunga", MON)
    [call] = client.calls
    assert call["model"] == "claude-haiku-5-5" and call["max_tokens"] == 600
    assert call["output_config"]["effort"] == "low" and call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["format"]["schema"]["additionalProperties"] is False
    for banned in ("temperature", "top_p", "top_k", "thinking", "tool_choice"):
        assert banned not in call                       # removed or rejected on the current models
    assert call["messages"][0]["role"] == "user"


def test_model_can_be_chosen_from_the_environment(store):
    a = Assistant(store, client=FakeClient(reply()), environ={"COLLECTOS_AI_MODEL": "claude-opus-5-5"})
    a.read_reply("Friday tak 50 hazaar", MON)
    assert a.model == "claude-opus-5-5"
    assert a._client.calls[0]["model"] == "claude-opus-5-5"


def test_only_masked_text_and_the_date_are_sent_and_the_call_is_logged(store):
    a, client = assistant(store, reply(quote="Friday tak 50 hazaar"))
    a.read_reply("Hi, 98765 43210 here. mail a@b.co. Friday tak 50 hazaar", MON)
    sent = client.calls[0]["messages"][0]["content"]
    assert "98765" not in sent and "a@b.co" not in sent and "[phone]" in sent and "[email]" in sent
    assert "Monday, 2026-10-05" in sent and "Friday tak 50 hazaar" in sent
    entry = [e for e in store.audit_entries() if e.action == "ai.request"][0]
    assert entry.actor == "priya" and "98765" not in entry.after["sent"] and entry.after["feature"] == "reply_reader"
    assert store.ai_month_summary()["calls"] == 1


def test_usage_is_recorded_even_when_the_answer_is_unusable(store):
    a, _ = assistant(store, "not json at all")
    with pytest.raises(AiBadOutput):
        a.read_reply("Friday tak 50 hazaar", MON)
    assert store.ai_month_summary()["calls"] == 1 and store.ai_month_summary()["cost_micro_usd"] > 0


# ---- reply reader: the answer is untrusted ----------------------------------------------------------------------------------------


def test_good_answer_is_accepted(store):
    a, _ = assistant(store, reply())
    r = a.read_reply("Bhai Friday tak 50 hazaar bhej dunga", MON)
    assert (r["intent"], r["promise_date"], r["promise_amount"], r["confidence"], r["evidence_found"]) == ("promise_to_pay", "2026-10-09", "50000.00", "high", True)
    assert r["dispute_category"] is None and r["warnings"] == [] and r["quote"] == "Friday tak 50 hazaar"


def test_quote_matching_ignores_case_and_spacing(store):
    a, _ = assistant(store, reply(quote="friday   TAK 50 hazaar"))
    assert a.read_reply("Bhai Friday tak 50 hazaar bhej dunga", MON)["evidence_found"] is True


def test_a_promise_with_no_supporting_quote_is_dropped(store):
    a, _ = assistant(store, reply(quote="he promised on Friday"))     # these words are not in the message
    r = a.read_reply("Bhai Friday tak 50 hazaar bhej dunga", MON)
    assert r["promise_date"] is None and r["promise_amount"] is None and r["confidence"] == "low" and r["evidence_found"] is False
    assert any("could not point to the words" in w for w in r["warnings"])


def test_an_empty_quote_also_drops_the_promise(store):
    a, _ = assistant(store, reply(quote=""))
    assert a.read_reply("Friday tak 50 hazaar", MON)["promise_date"] is None


@pytest.mark.parametrize("bad_date", ["2026-10-04", "2025-01-01", "2028-01-01", "next friday", "2026-13-40"])
def test_impossible_dates_are_ignored_with_a_warning(store, bad_date):
    a, _ = assistant(store, reply(promise_date=bad_date))
    r = a.read_reply("Friday tak 50 hazaar", MON)
    assert r["promise_date"] is None and r["warnings"] and r["promise_amount"] == "50000.00"


def test_today_is_an_acceptable_promise_date(store):
    a, _ = assistant(store, reply(promise_date="2026-10-05"))
    assert a.read_reply("Friday tak 50 hazaar", MON)["promise_date"] == "2026-10-05"


@pytest.mark.parametrize("bad_amount", ["-5", "0", "abc", "12.345", "99999999999"])
def test_impossible_amounts_are_ignored(store, bad_amount):
    a, _ = assistant(store, reply(promise_amount=bad_amount))
    r = a.read_reply("Friday tak 50 hazaar", MON)
    assert r["promise_amount"] is None and r["warnings"]


def test_amounts_with_commas_or_currency_signs_are_normalised(store):
    a, _ = assistant(store, reply(promise_amount="₹ 1,50,000"))
    assert a.read_reply("Friday tak 50 hazaar", MON)["promise_amount"] == "150000.00"


def test_dispute_replies_get_a_category(store):
    a, _ = assistant(store, reply(intent="dispute", promise_date="", promise_amount="", dispute_category="price", quote="rate galat hai", summary="Says the rate is wrong"))
    r = a.read_reply("Sir rate galat hai quotation se alag", MON)
    assert r["intent"] == "dispute" and r["dispute_category"] == "price" and r["promise_date"] is None


def test_dispute_without_a_category_falls_back_to_other(store):
    a, _ = assistant(store, reply(intent="dispute", promise_date="", promise_amount="", dispute_category="none", quote="galat", summary="x"))
    assert a.read_reply("bill galat hai", MON)["dispute_category"] == "other"


def test_summary_is_trimmed_and_never_empty(store):
    a, _ = assistant(store, reply(summary="x" * 500), reply(summary="   "))
    assert len(a.read_reply("Friday tak 50 hazaar", MON)["summary"]) == 160
    assert a.read_reply("Friday tak 50 hazaar", MON)["summary"].startswith("Customer replied")


# ---- malformed or hostile model output ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", [
    "not json", "[]", '"text"', json.dumps({"intent": "promise_to_pay"}),
    json.dumps({**reply(), "extra": "field"}), json.dumps({**reply(), "intent": "something_else"}),
    json.dumps({**reply(), "promise_amount": 50000}), json.dumps({**reply(), "confidence": "certain"}),
])
def test_malformed_answers_are_rejected(store, answer):
    a, _ = assistant(store, answer)
    with pytest.raises(AiBadOutput):
        a.read_reply("Friday tak 50 hazaar", MON)


def test_refusals_and_cut_off_answers_are_reported(store):
    a, _ = assistant(store, reply(), client_kw={"stop_reason": "refusal"})
    with pytest.raises(AiRefused):
        a.read_reply("Friday", MON)
    a, _ = assistant(store, reply(), client_kw={"stop_reason": "max_tokens"})
    with pytest.raises(AiBadOutput, match="cut off"):
        a.read_reply("Friday", MON)


@pytest.mark.parametrize("name, expected_type, fragment", [
    ("AuthenticationError", AiUnavailable, "key was rejected"), ("PermissionDeniedError", AiUnavailable, "key was rejected"),
    ("NotFoundError", AiUnavailable, "model is not available"), ("RateLimitError", AiError, "busy"),
    ("APIConnectionError", AiError, "internet"), ("BadRequestError", AiError, "could not process"), ("WeirdError", AiError, "WeirdError"),
])
def test_service_errors_become_safe_messages(store, name, expected_type, fragment):
    boom = type(name, (Exception,), {})("secret details that must not be shown")
    a, _ = assistant(store, boom)
    with pytest.raises(expected_type) as info:
        a.read_reply("Friday tak 50 hazaar", MON)
    assert fragment in str(info.value) and "secret details" not in str(info.value)


def test_input_validation(store):
    a, client = assistant(store, reply())
    with pytest.raises(AiInputError, match="Paste"):
        a.read_reply("   ", MON)
    with pytest.raises(AiInputError, match="longer than"):
        a.read_reply("x" * 2001, MON)
    assert client.calls == []


# ---- the column helper ------------------------------------------------------------------------------------------------------------------

TALLY_LIKE = [
    (1, ["Demo Company Pvt Ltd", None, None, None, None]),
    (2, ["Bills Receivable", None, None, None, None]),
    (3, ["Date", "Ref. No.", "Party's Name", "Pending Amount", "Due on"]),
    (4, ["1-Sep-2026", "INV-10", "Sharma Steel", "54000.00", "1-Oct-2026"]),
    (5, ["5-Sep-2026", "INV-11", "Gupta Agencies", "12,500.00", "5-Oct-2026"]),
]


def test_masking_keeps_titles_and_headers_but_never_data():
    text = mask_rows(TALLY_LIKE)
    assert "Demo Company Pvt Ltd" in text and "Party's Name" in text and "Pending Amount" in text
    for secret in ("Sharma Steel", "Gupta Agencies", "54000", "12,500", "INV-10", "1-Sep-2026"):
        assert secret not in text
    assert "<date>" in text and "<number>" in text and "<text:12>" in text


def test_masking_redacts_identifiers_in_title_rows():
    text = mask_rows([(1, ["GSTIN 27AAPFU0939F1ZV  mail a@b.co", None]), (2, ["Date", "Amount"]), (3, ["1-Sep-2026", "100"])])
    assert "27AAPFU" not in text and "a@b.co" not in text and "[gstin]" in text


def test_masking_limits_rows_and_columns():
    rows = [(i, ["h"] * 30) for i in range(1, 100)]
    text = mask_rows(rows)
    assert len(text.splitlines()) == 40 and text.splitlines()[0].count("|") == 16


def good_map(**over):
    base = {"header_row": 3, "columns": {"invoice_no": 2, "customer": 3, "invoice_date": 1, "due_date": 5, "amount": 0, "paid": 0, "outstanding": 4},
            "notes": "Only a pending amount is present", "confidence": "high"}
    base.update(over)
    return base


def test_column_helper_end_to_end_then_import_with_the_mapping(store):
    a, client = assistant(store, good_map())
    suggestion = a.map_columns([("Sheet1", TALLY_LIKE)])
    assert suggestion["header_row"] == 3 and suggestion["columns"]["customer"] == 3 and suggestion["sheet"] == "Sheet1"
    sent = client.calls[0]["messages"][0]["content"]
    assert "Sharma Steel" not in sent and "54000" not in sent                       # data never leaves
    cm = ColumnMap("Sheet1", suggestion["header_row"], {f: c - 1 for f, c in suggestion["columns"].items() if c})
    report = import_sheets([("Sheet1", TALLY_LIKE)], column_map=cm)
    assert [i.invoice.invoice_no for i in report.invoices] == ["INV-10", "INV-11"]
    assert report.invoices[0].invoice.outstanding == Decimal("54000.00")


@pytest.mark.parametrize("data, fragment", [
    (good_map(header_row=99), "not in the file"), (good_map(header_row="3"), "malformed"),
    (good_map(columns={"invoice_no": 2}), "does not cover"), (good_map(columns={**good_map()["columns"], "customer": 99}), "not a column"),
    (good_map(columns={**good_map()["columns"], "customer": 2}), "same column"),
    (good_map(columns={**good_map()["columns"], "customer": 0}), "does not cover: customer"),
    (good_map(columns={**good_map()["columns"], "outstanding": 0}), "amount or outstanding"),
    (good_map(columns={**good_map()["columns"], "amount": True}), "not a column"),
])
def test_bad_mappings_are_rejected_before_they_can_touch_an_import(data, fragment):
    with pytest.raises(AiBadOutput, match=fragment):
        validate_column_map(data, TALLY_LIKE, "Sheet1")


def test_the_first_table_like_sheet_is_used():
    cover = [(1, ["Quarterly pack"])]
    name, rows = ai.pick_sheet([("Cover", cover), ("Bills", TALLY_LIKE)])
    assert name == "Bills" and rows is TALLY_LIKE


# ---- the importer with an explicit mapping ----------------------------------------------------------------------------------------------------


def test_importer_without_a_mapping_says_so_and_with_one_succeeds():
    odd = [(1, ["X", "Y", "Z"]), (2, ["A1", "Acme", "01-09-2026"]), (3, ["A2", "Beta", "02-09-2026"])]
    with pytest.raises(NoInvoiceTable):
        import_sheets([("S", odd)])
    assert issubclass(NoInvoiceTable, ImportFileError)


def test_mapping_errors_are_clear():
    sheets = [("S", TALLY_LIKE)]
    base = {"invoice_no": 1, "customer": 2, "invoice_date": 0, "outstanding": 3}
    for cm, fragment in [
        (ColumnMap("Nope", 3, base), "not in the file"), (ColumnMap("S", 77, base), "not in the sheet"),
        (ColumnMap("S", 3, {**base, "bogus": 4}), "unknown fields"), (ColumnMap("S", 3, {**base, "customer": 1}), "same column"),
        (ColumnMap("S", 3, {"invoice_no": 1}), "needs invoice number"), (ColumnMap("S", 3, {**base, "customer": -1}), "invalid column"),
    ]:
        with pytest.raises(ImportFileError, match=fragment):
            import_sheets(sheets, column_map=cm)


def test_a_wrong_mapping_shows_up_as_row_errors_not_bad_data():
    # dates read as customer and so on: every row fails validation instead of being saved
    swapped = ColumnMap("S", 3, {"invoice_no": 0, "customer": 1, "invoice_date": 2, "outstanding": 3})
    report = import_sheets([("S", TALLY_LIKE)], column_map=swapped)
    assert report.invoices == [] and len(report.errors) == 2


# ---- remittance reader --------------------------------------------------------------------------------------------------------------------------


def remit(**over):
    base = {"payer": "Acme Traders Pvt Ltd", "payment_date": "2026-10-03", "reference": "UTR123456", "total_received": "97,000", "total_tds": "2000",
            "invoices": [{"invoice_no": "INV-1042", "gross": "50000", "tds": "1000", "net": "49000"}, {"invoice_no": "INV-1043", "gross": "50000", "tds": "1000", "net": "48000"}],
            "notes": "none", "confidence": "high"}
    base.update(over)
    return base


def test_remittance_reader_sends_the_document_and_validates_amounts(store):
    a, client = assistant(store, remit())
    r = a.read_remittance(b"%PDF-1.4 fake", "application/pdf")
    block = client.calls[0]["messages"][0]["content"][0]
    assert block["type"] == "document" and block["source"]["media_type"] == "application/pdf" and block["source"]["data"] == base64.standard_b64encode(b"%PDF-1.4 fake").decode()
    assert r["total_received"] == "97000.00" and r["total_tds"] == "2000.00" and [i["invoice_no"] for i in r["invoices"]] == ["INV-1042", "INV-1043"]
    assert r["invoices"][0]["net"] == "49000.00" and r["payment_date"] == "2026-10-03" and r["confidence"] == "high" and r["warnings"] == []
    entry = [e for e in store.audit_entries() if e.action == "ai.request"][0]
    assert entry.after["feature"] == "remittance_reader" and "JVBERi" not in entry.after["sent"] and "document" in entry.after["sent"]   # the file itself is never stored in the log


def test_remittance_images_use_an_image_block(store):
    a, client = assistant(store, remit())
    a.read_remittance(b"\x89PNG fake", "image/png")
    assert client.calls[0]["messages"][0]["content"][0]["type"] == "image"


def test_remittance_totals_that_do_not_add_up_are_flagged_not_fixed(store):
    a, _ = assistant(store, remit(total_received="90000"))
    r = a.read_remittance(b"x", "application/pdf")
    assert r["total_received"] == "90000.00" and r["confidence"] == "low" and any("do not add up" in w for w in r["warnings"])


def test_remittance_bad_values_are_dropped_with_warnings(store):
    a, _ = assistant(store, remit(payment_date="31-31-2026", total_tds="abc", invoices=[{"invoice_no": "  ", "gross": "", "tds": "", "net": ""}, {"invoice_no": "INV-9", "gross": "x", "tds": "", "net": "100"}]))
    r = a.read_remittance(b"x", "application/pdf")
    assert r["payment_date"] is None and r["total_tds"] is None and [i["invoice_no"] for i in r["invoices"]] == ["INV-9"]
    assert r["invoices"][0]["gross"] is None and len(r["warnings"]) >= 3 and r["confidence"] == "low"


@pytest.mark.parametrize("data, mime, fragment", [(b"x", "text/html", "PDF or a PNG"), (b"x" * (5 * 1024 * 1024 + 1), "application/pdf", "larger than 5 MB")])
def test_remittance_input_limits(store, data, mime, fragment):
    a, client = assistant(store, remit())
    with pytest.raises(AiInputError, match=fragment):
        a.read_remittance(data, mime)
    assert client.calls == []


def test_check_connection_proves_the_schema_round_trip():
    r = ai.check_connection(FakeClient(reply()), "claude-haiku-5-5")
    assert r["answer"]["intent"] == "promise_to_pay" and r["cost_micro_usd"] == estimate_cost_micro_usd("claude-haiku-5-5", 300, 60)
