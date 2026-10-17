/* CollectOS dashboard: the extras that make the weekly cycle easier and give owners something beyond a reminder list:
   morning brief, statement of account, interest and waiver offer, "can I ship this order?", cash calendar and the
   settings behind them. Loaded after features.js. Text from the server is untrusted and only inserted with textContent. */
(() => {
  "use strict";
  const CO = window.CO;
  const { h, icon, fmt, fmt0, dmy, dm, plural, state, store, toast, post, getJSON, load, writable, actor, custLink, copy, viewHead, dlg, field } = CO;
  const { card, note, spinner, back, simpleTable } = CO.ui;

  const num = (s) => Number(String(s).replace(/[₹,\s]/g, ""));
  const cleanAmount = (s) => String(s).replace(/[₹,\s]/g, "").replace(/^rs\.?/i, "");
  // redraw the current screen after a change, and put the page back where the person was
  async function reload() { const y = window.scrollY; await load(); setTimeout(() => window.scrollTo(0, y), 300); }

  /* ---------- a message window that can be sent from your own WhatsApp / mail app; you press send ---------- */
  // variants: [{ label, wa, email, subject, log }]. The person edits the text, opens their own app and presses send.
  function composer({ title, sub, customer, contact, variants, summary, downloads = [] }) {
    dlg.classList.add("wide");
    let vi = 0, channel = "whatsapp";
    const text = h("textarea", { id: "c-text", class: "draft-edit", "aria-label": "Message text" });
    const subject = h("input", { id: "c-subject", "aria-label": "Email subject" });
    const variantSeg = h("div", { class: "seg" }), chanSeg = h("div", { class: "seg" }), actions = h("div", { class: "send-actions" }), confirm = h("div");
    const blocked = contact.do_not_contact;
    const fill = () => { const v = variants[vi]; text.value = channel === "whatsapp" ? v.wa : v.email; subject.value = v.subject; subject.hidden = channel !== "email"; };
    const waUrl = () => (contact.phone ? `https://wa.me/${contact.phone.replace("+", "")}?text=${encodeURIComponent(text.value)}` : null);
    const mailUrl = () => (contact.email ? `mailto:${contact.email}?subject=${encodeURIComponent(subject.value)}&body=${encodeURIComponent(text.value)}` : null);
    function sentPrompt(via) {
      if (!writable()) return;
      confirm.replaceChildren(h("div", { class: "note" }, h("strong", { text: "Did you send it? " }), "If so, record it so the list knows this customer was contacted.",
        h("div", { class: "modal-actions", style: { "margin-top": "10px" } },
          h("button", { type: "button", class: "btn ghost", text: "Not yet", onclick: () => confirm.replaceChildren() }),
          h("button", { type: "button", class: "btn", text: "Yes, log it", onclick: async (e) => {
            e.currentTarget.disabled = true;
            try { await post("/api/followup", { customer, channel: via, outcome: `Sent ${variants[vi].log} (${via === "whatsapp" ? "WhatsApp" : "email"})`, actor: actor() }); dlg.close(); toast("Logged as contacted."); await load(true); }
            catch (ex) { toast(ex.message); e.currentTarget.disabled = false; }
          } }))));
    }
    function draw() {
      variantSeg.replaceChildren(...variants.map((v, i) => h("button", { type: "button", "aria-pressed": String(vi === i), text: v.label, onclick: () => { vi = i; fill(); draw(); } })));
      variantSeg.hidden = variants.length < 2;
      chanSeg.replaceChildren(...[["whatsapp", "WhatsApp"], ["email", "Email"]].map(([id, t]) => h("button", { type: "button", "aria-pressed": String(channel === id), text: t, onclick: () => { channel = id; fill(); draw(); } })));
      const open = channel === "whatsapp"
        ? (contact.phone && !blocked ? h("a", { class: "btn", href: waUrl(), target: "_blank", rel: "noopener noreferrer", onclick: (e) => { e.currentTarget.href = waUrl(); sentPrompt("whatsapp"); } }, icon("chat"), "Open WhatsApp")
          : h("button", { type: "button", class: "btn", disabled: true }, icon("chat"), "Open WhatsApp"))
        : (contact.email && !blocked ? h("a", { class: "btn", href: mailUrl(), onclick: (e) => { e.currentTarget.href = mailUrl(); sentPrompt("email"); } }, icon("reminders"), "Open email")
          : h("button", { type: "button", class: "btn", disabled: true }, icon("reminders"), "Open email"));
      actions.replaceChildren(open,
        h("button", { type: "button", class: "btn ghost", onclick: () => copy(channel === "email" ? `Subject: ${subject.value}\n\n${text.value}` : text.value) }, icon("copy"), "Copy"),
        ...downloads.map((dl) => h("a", { class: "btn ghost", href: dl.href, download: "" }, icon("download"), dl.label)));
      body.replaceChildren(...[
        blocked ? h("div", { class: "form-error", text: "This customer is marked do-not-contact. Handle them outside the daily list." }) : null,
        !blocked && channel === "whatsapp" && !contact.phone ? note("No phone number saved yet, so WhatsApp cannot open. Add one on the customer page.") : null,
        !blocked && channel === "email" && !contact.email ? note("No email saved yet, so the mail app cannot open. Add one on the customer page.") : null,
        channel === "email" ? h("div", { class: "field" }, h("label", { for: "c-subject", text: "Subject" }), subject) : null,
        h("div", { class: "field" }, h("label", { for: "c-text", text: "Message (you can edit it before sending)" }), text),
        downloads.length ? h("p", { class: "sub", text: "Attach the downloaded file to your email or WhatsApp message yourself." }) : null,
        h("p", { class: "sub", text: "This opens your own WhatsApp or mail app with the text filled in. You read it and press send; CollectOS never sends anything itself." })].filter(Boolean));
    }
    const body = h("div");
    fill();
    const head = h("div", { class: "send-head" }, h("div", {}, h("h2", { id: "modalTitle", text: title }), h("p", { class: "sub", text: sub })), h("div", { class: "send-segs" }, variantSeg, chanSeg));
    dlg.replaceChildren(h("div", { class: "modal-inner" }, head, summary, body, confirm,
      h("div", { class: "modal-actions", style: { "justify-content": "space-between", "flex-wrap": "wrap" } }, actions, h("button", { type: "button", class: "btn ghost", text: "Close", onclick: () => dlg.close() }))));
    draw();
  }

  const failure = (e) => { dlg.replaceChildren(h("div", { class: "modal-inner" }, h("div", { class: "form-error", text: e.message }), h("div", { class: "modal-actions" }, h("button", { type: "button", class: "btn ghost", text: "Close", onclick: () => dlg.close() })))); };
  async function loading(text) { dlg.classList.add("wide"); dlg.replaceChildren(h("div", { class: "modal-inner" }, spinner(text))); if (!dlg.open) dlg.showModal(); }
  const baseParams = () => ({ sender: store.get("sender", ""), company: store.get("company", ""), language: store.get("lang", "en") });

  /* ---------- statement of account and balance confirmation ---------- */
  async function openStatement(customer) {
    await loading("Preparing the statement…");
    let d;
    try { d = await getJSON("/api/account-statement", { customer, ...baseParams() }); } catch (e) { return failure(e); }
    const file = new URLSearchParams({ customer, as_of: d.as_of, company: store.get("company", "") });
    const summary = h("div", { class: "stmt" },
      d.rows.length ? simpleTable(["Invoice", "Date", "Due", "Balance", "Days overdue"],
        d.rows.map((r) => [r.invoice_no, dmy(r.invoice_date), r.due_date ? dmy(r.due_date) : "none", fmt(r.balance), r.days_overdue ? String(r.days_overdue) : "—"]), { right: [3, 4] })
        : note("No open invoices for this customer."),
      h("p", { class: "sub", text: `Closing balance ${fmt(d.balance)}, of which overdue ${fmt(d.overdue)}. Only the open items in your data are listed; no payment history is invented.` }));
    composer({
      title: `Statement for ${d.customer}`, sub: `Open items as on ${dmy(d.as_of)}. Sending this answers "please send the ledger" in one go.`, customer: d.customer, contact: d.contact, summary,
      variants: [{ label: "Statement", wa: d.message, email: d.message, subject: d.subject, log: "statement of account" },
        { label: "Balance confirmation", wa: d.confirmation, email: d.confirmation, subject: "Balance confirmation request", log: "balance confirmation request" }],
      downloads: [{ label: "Download Excel", href: "/account-statement.xlsx?" + file }],
    });
  }

  /* ---------- interest and waiver offer ---------- */
  async function openInterest(customer, waiverDays = 7) {
    await loading("Working out the interest…");
    let d;
    try { d = await getJSON("/api/interest", { customer, waiver_days: String(waiverDays), ...baseParams() }); } catch (e) { return failure(e); }
    const msmed = d.msmed && d.msmed.rows.length ? h("div", { class: "msmed" }, h("h3", { text: "MSMED Act estimate (for your CA)" }),
      simpleTable(["Invoice", "Overdue amount", "Days past the appointed day", "Estimated interest"], d.msmed.rows.map((r) => [r.invoice_no, fmt(r.outstanding), String(r.delay_days), fmt(r.interest)]), { right: [1, 2, 3] }),
      h("p", { class: "sub", text: `Total about ${fmt(d.msmed.total)} at ${d.msmed.rate}% a year (three times the RBI bank rate of ${d.msmed.bank_rate}%), compounded monthly. ${d.msmed.note}` })) : null;
    if (!d.configured) {
      dlg.replaceChildren(h("div", { class: "modal-inner" }, h("h2", { id: "modalTitle", text: "Interest and waiver offer" }), note(d.note), msmed,
        h("p", { class: "sub", text: "Enter the interest rate from your own invoice or purchase-order terms. CollectOS does not know what was agreed with your customers." }),
        h("div", { class: "modal-actions" }, h("button", { type: "button", class: "btn ghost", text: "Close", onclick: () => dlg.close() }),
          h("a", { class: "btn", href: "#/settings", text: "Open settings", onclick: () => dlg.close() }))));
      return;
    }
    const days = h("input", { id: "w-days", type: "number", min: "1", max: "30", value: String(d.waiver_days), "aria-label": "Days the offer stays open" });
    const summary = h("div", { class: "stmt" },
      h("div", { class: "note warn", text: d.terms_note }),
      d.msmed_note && !d.msmed ? h("p", { class: "sub", text: d.msmed_note }) : null,
      d.rows.length ? h("h3", { text: "At the rate in your own terms", style: { "margin-top": "14px" } }) : null,
      d.rows.length ? simpleTable(["Invoice", "Overdue amount", "Days past due", "Interest"], d.rows.map((r) => [r.invoice_no, fmt(r.outstanding), String(r.days_overdue), fmt(r.interest)]), { right: [1, 2, 3] })
        : note("No interest has accrued yet (everything is within the grace days, not overdue, or in dispute)."),
      d.rows.length ? h("p", { class: "sub", text: `Simple interest at ${d.rate}% a year${d.grace_days ? `, after ${d.grace_days} grace days` : ""}: ${fmt(d.interest)} on ${fmt(d.overdue)} overdue.` }) : null,
      d.disputed_left_out.length ? h("p", { class: "sub", text: `Left out because they are in dispute: ${d.disputed_left_out.join(", ")}.` }) : null,
      h("div", { class: "field inline" }, h("label", { for: "w-days", text: "Offer stays open (days)" }), days,
        h("button", { type: "button", class: "btn small ghost", text: "Update draft", onclick: () => openInterest(customer, Number(days.value) || 7) })),
      h("p", { class: "sub", text: "Offering a waiver is your commercial decision. The draft promises it only if the full overdue amount arrives by the date shown." }),
      msmed);
    if (!d.draft) {
      dlg.replaceChildren(h("div", { class: "modal-inner" }, h("h2", { id: "modalTitle", text: `Interest for ${d.customer}` }), summary,
        h("div", { class: "modal-actions" }, h("button", { type: "button", class: "btn ghost", text: "Close", onclick: () => dlg.close() }))));
      return;
    }
    composer({
      title: `Interest waiver offer for ${d.customer}`, sub: `Interest accrued: ${fmt(d.interest)} · offer open until ${dmy(d.deadline)}`, customer: d.customer, contact: d.contact, summary,
      variants: [{ label: "Waiver offer", wa: d.draft.whatsapp, email: d.draft.email, subject: d.draft.subject, log: "interest waiver offer" }],
    });
  }

  CO.extra.customerActions = (d) => [
    h("button", { type: "button", class: "btn small ghost", onclick: () => openStatement(d.customer) }, icon("download"), "Statement"),
    Number(d.summary.overdue) > 0 ? h("button", { type: "button", class: "btn small ghost", onclick: () => openInterest(d.customer) }, icon("money"), "Interest & waiver") : null,
    h("a", { class: "btn small ghost", href: "#/shipcheck/" + encodeURIComponent(d.customer) }, icon("check"), "Ship check"),
  ];
  CO.openStatement = openStatement; CO.openInterest = openInterest;

  /* ---------- morning brief on the dashboard ---------- */
  CO.extra.dashboardTop = ((previous) => (d) => {
    const base = previous ? previous(d) : [];
    const pre = h("pre", { class: "pre brief-text", text: "Preparing the brief…" }), actions = h("div", { class: "head-actions" });
    getJSON("/api/brief", { company: store.get("company", "") }).then((j) => {
      pre.textContent = j.text;
      actions.replaceChildren(h("button", { type: "button", class: "btn small ghost", onclick: () => copy(j.text) }, icon("copy"), "Copy"),
        h("a", { class: "btn small", href: "https://wa.me/" + "?text=" + encodeURIComponent(j.text), target: "_blank", rel: "noopener noreferrer" }, icon("chat"), "Open in WhatsApp"));
    }).catch((e) => { pre.textContent = "Could not build the brief: " + e.message; });
    return [...base, card("c12", 10, h("div", { class: "card-head" }, h("h2", { text: "Morning brief" }), actions), pre,
      h("p", { class: "sub", text: "A short summary to paste into your team's WhatsApp group. Built from today's work queue and promises; nothing is sent from here." }))];
  })(CO.extra.dashboardTop);

  /* ---------- "can I ship this order?" ---------- */
  const VERDICT = { ship: "ship", ship_with_care: "care", advance: "advance", hold: "hold", new_customer: "new" };
  CO.registerView("shipcheck", "Ship check", "check", (d, preset) => {
    const names = h("datalist", { id: "ship-names" }, d.customers.map((c) => h("option", { value: c.customer })));
    const who = h("input", { id: "ship-who", list: "ship-names", placeholder: "Customer name", autocomplete: "off", value: preset || "" });
    const order = h("input", { id: "ship-order", inputmode: "decimal", placeholder: "Order value, e.g. 150000" });
    const out = h("div");
    async function run(e) {
      if (e) e.preventDefault();
      if (!who.value.trim() || !cleanAmount(order.value)) { out.replaceChildren(note("Enter the customer and the order value.")); return; }
      out.replaceChildren(spinner("Checking…"));
      try {
        const j = await getJSON("/api/shipcheck", { customer: who.value.trim(), order: cleanAmount(order.value) });
        const f = j.figures || {};
        out.replaceChildren(h("div", { class: `verdict ${VERDICT[j.verdict]}` }, h("div", { class: "vhead" }, h("span", { class: "vdot" }), h("h2", { text: j.headline })),
          h("p", { class: "sub", text: `${j.customer}, order of ${fmt(j.order_value)}` }),
          j.advance ? h("div", { class: "note warn", text: `Ask for at least ${fmt(j.advance)} as an advance to stay within the credit limit.` }) : null,
          h("ul", { class: "briefing" }, j.reasons.map((r) => h("li", { class: `brief ${r.level === "bad" ? "bad" : r.level}` }, icon(r.level === "ok" ? "check" : r.level === "info" ? "chat" : "alert"), h("span", { text: r.text })))),
          f.outstanding === undefined ? null : h("dl", { class: "facts" },
            h("dt", { text: "Owes now" }), h("dd", { text: fmt(f.outstanding) }), h("dt", { text: "Overdue" }), h("dd", { text: f.oldest_days ? `${fmt(f.overdue)} (oldest ${f.oldest_days} days)` : fmt(f.overdue) }),
            h("dt", { text: "After this order" }), h("dd", { text: fmt(f.after_order) }), h("dt", { text: "Credit limit" }), h("dd", { text: f.credit_limit ? fmt(f.credit_limit) : "None set" })),
          f.outstanding === undefined ? null : h("a", { class: "btn small ghost", href: "#/customer/" + encodeURIComponent(j.customer), style: { "margin-top": "14px" } }, icon("customers"), "Open customer"),
          h("details", { class: "why" }, h("summary", { text: "The rules behind this answer" }), h("ul", { class: "sendlist" }, j.rules.map((r) => h("li", { text: r })))),
          h("p", { class: "sub", text: "A rule of thumb from your own data, not a decision. Nothing is blocked; you and your sales team decide." })));
      } catch (ex) { out.replaceChildren(h("div", { class: "banner", text: ex.message })); }
    }
    const form = h("form", { class: "shipform", novalidate: true, onsubmit: run }, h("div", { class: "field" }, h("label", { for: "ship-who", text: "Customer" }), who, names),
      h("div", { class: "field" }, h("label", { for: "ship-order", text: "Order value (₹)" }), order), h("button", { type: "submit", class: "btn", text: "Check" }));
    if (preset) setTimeout(() => order.focus(), 0);
    return h("div", {}, viewHead("Can I ship this order?", "Check a customer before dispatch, using what your data already shows: dues, credit limit, broken promises and disputes. One shared rule for sales and accounts."),
      h("div", { class: "grid" }, card("c5 r-c12", 0, form), card("c7 r-c12", 1, out)));
  });

  /* ---------- cash calendar ---------- */
  CO.registerView("cashflow", "Cash calendar", "money", async (d) => {
    let j;
    try { j = await getJSON("/api/cashcal"); } catch (e) { return h("div", { class: "banner", text: e.message }); }
    const t = j.totals, out = j.outgoings, monthly = num(out.monthly);
    const kpi = (i, label, value, hint, link) => {
      const el = h("div", { class: "kpi mini glass reveal c3 r-c6", style: { "--i": i } }, h("div", { class: "label", text: label }), h("div", { class: "value plain num", text: value }), h("div", { class: "hint", text: hint }));
      if (link) { el.classList.add("clickable"); el.tabIndex = 0; el.addEventListener("click", () => { location.hash = link; }); el.addEventListener("keydown", (e) => { if (e.key === "Enter") location.hash = link; }); }
      return el;
    };
    const gap = num(out.gap_30_firm);
    const kpis = [
      kpi(0, "Firm, next 14 days", fmt0(num(t.firm_14)), "Promises and invoices due, from reliable payers"),
      kpi(1, "Firm, next 30 days", fmt0(num(t.firm_30)), `${fmt0(num(t.all_30))} if the shaky ones also pay`),
      kpi(2, "Overdue, no date", fmt0(num(j.undated.amount)), `${plural(j.undated.customers, "customer")} with no promise date. Not counted.`, "#/queue"),
      monthly > 0 ? kpi(3, "30 days vs outgoings", (gap >= 0 ? "+" : "−") + fmt0(Math.abs(gap)), gap >= 0 ? `Firm money covers your ${fmt0(monthly)} fixed outgoings` : `Firm money falls ${fmt0(Math.abs(gap))} short of ${fmt0(monthly)} fixed outgoings`)
        : kpi(3, "30 days vs outgoings", "Not set", "Add your monthly fixed outgoings in Settings", "#/settings"),
    ];
    const max = Math.max(1, ...j.weeks.map((w) => num(w.total)));
    const weeks = j.weeks.map((w) => h("details", { class: "weekrow" },
      h("summary", {}, h("span", { class: "wk", text: `${dm(w.start)} – ${dm(w.end)}` }),
        h("span", { class: "weekbar" }, h("i", { class: "firm", style: { "--w": (num(w.firm) / max) * 100 + "%" } }), h("i", { class: "shaky", style: { "--w": (num(w.shaky) / max) * 100 + "%" } })),
        h("span", { class: "num amt", text: num(w.total) ? fmt0(num(w.total)) : "—" })),
      w.items.length ? h("ul", { class: "weekitems" }, w.items.map((i) => h("li", {}, h("span", { class: "sub", text: dm(i.date) }), custLink(i.customer),
        h("span", { class: "chip muted", text: i.kind === "promise" ? "their promise" : i.kind === "cheque" ? "cheque in hand" : "due by terms" }), h("span", { class: `chip ${i.firm ? "pending" : "broken"}`, text: i.firm ? "firm" : "shaky" }),
        h("span", { class: "num", text: fmt0(num(i.amount)) })))) : h("p", { class: "sub", text: "Nothing with a date this week." })));
    const legend = h("div", { class: "legend-cal" }, h("span", {}, h("i", { class: "swatch firm" }), "Firm"), h("span", {}, h("i", { class: "swatch shaky" }), "Shaky (broke more promises than kept, or over 30 days late)"));
    const undated = card("c4 r-c12", 6, h("h2", { text: "Overdue with no date", style: { "text-align": "left", "margin-bottom": "10px" } }),
      j.undated.top.length ? h("ul", { class: "briefing" }, j.undated.top.map((u) => h("li", { class: "brief" }, h("span", {}, custLink(u.customer)), h("span", { class: "num", style: { flex: "none" }, text: `${fmt0(num(u.amount))} · ${u.oldest_days}d` }))))
        : h("div", { class: "empty", text: "Everything overdue has a promise date." }),
      h("p", { class: "sub", text: "Do not count on this money. Get a promise date for each customer (log it as a follow-up) and it will appear in the calendar." }),
      j.promises_without_amount ? h("p", { class: "sub", text: `${plural(j.promises_without_amount, "promise")} without an amount ${j.promises_without_amount === 1 ? "is" : "are"} not counted either.` }) : null);
    return h("div", {}, viewHead("Cash calendar", "Money that has a date on it, week by week, against your fixed monthly outgoings. A planning view, not a prediction."),
      h("div", { class: "grid" }, ...kpis, card("c8 r-c12", 5, h("div", { class: "card-head" }, h("h2", { text: "Next five weeks" })), h("div", { class: "weeks" }, weeks), legend), undated,
        card("c12", 7, note(j.note))));
  });

  /* ---------- settings behind the extras ---------- */
  CO.extra.settingsMore = (d) => {
    const s = d.settings;
    const mk = (id, label, value, placeholder, hint, mode) => ({ id, key: label[0], el: h("input", { id: `x-${id}`, value, placeholder, inputmode: mode || "text", autocomplete: "off" }), label: label[1], hint });
    const rows = [
      mk("upi_id", ["upi_id", "Your UPI id"], s.upi_id, "name@bank", "Customers pay straight to this id. Reminders can show it, with a QR code."),
      mk("payee_name", ["payee_name", "Name shown to the payer"], s.payee_name, "Your business name"),
      mk("interest_rate", ["interest_rate", "Interest on overdue invoices (% a year)"], s.interest_rate === "0.00" ? "" : s.interest_rate, "e.g. 18, only if your invoice terms say so", "Used for the interest and waiver offer. Leave empty to switch it off.", "decimal"),
      mk("interest_grace_days", ["interest_grace_days", "Grace days before interest"], s.interest_grace_days === "0" ? "" : s.interest_grace_days, "0", "", "numeric"),
      mk("monthly_outgoings", ["monthly_outgoings", "Fixed monthly outgoings (₹)"], s.monthly_outgoings === "0.00" ? "" : s.monthly_outgoings, "salaries + GST + EMIs", "Shown against expected collections in the cash calendar.", "decimal"),
      mk("msmed_bank_rate", ["msmed_bank_rate", "RBI bank rate (% a year) for the MSMED estimate"], s.msmed_bank_rate === "0.00" ? "" : s.msmed_bank_rate, "enter the current rate", "Only used if you are a registered micro or small supplier (Settings). Leave empty to switch the estimate off.", "decimal"),
    ];
    const itc = h("input", { type: "checkbox", id: "x-itc", checked: s.itc_flag === "1" });
    itc.addEventListener("change", async () => {
      try { await post("/api/setting", { key: "itc_flag", value: itc.checked ? "1" : "0", actor: actor() }); toast("Saved"); await load(true); }
      catch (e) { itc.checked = !itc.checked; toast(e.message); }
    });
    const save = async (e) => {
      e.currentTarget.disabled = true;
      try {
        for (const r of rows) {
          const raw = r.el.value.trim();
          const value = r.key === "interest_rate" || r.key === "monthly_outgoings" || r.key === "msmed_bank_rate" ? (cleanAmount(raw) || "0") : r.key === "interest_grace_days" ? (raw || "0") : raw;
          const shown = { interest_rate: s.interest_rate, monthly_outgoings: s.monthly_outgoings, msmed_bank_rate: s.msmed_bank_rate }[r.key];
          const same = shown !== undefined ? Number(value) === Number(shown) : value === s[r.key];
          if (!same) await post("/api/setting", { key: r.key, value, actor: actor() });
        }
        toast("Saved"); await load(true);
      } catch (ex) { toast(ex.message); e.currentTarget.disabled = false; }
    };
    return card("c12", 3, h("h2", { text: "Payments, interest and cash planning", style: { "text-align": "left", "margin-bottom": "6px" } }),
      h("p", { class: "sub", text: "These stay in your local database. UPI details only appear in messages you choose to send." }),
      h("div", { class: "setgrid" }, rows.map((r) => h("div", { class: "field" }, h("label", { for: `x-${r.id}`, text: r.label }), r.el, r.hint ? h("span", { class: "hint", text: r.hint }) : null))),
      writable() ? h("div", { class: "field", style: { "margin-top": "6px" } }, h("label", { for: "x-itc", class: "checkrow" }, itc, h("span", { text: "Flag open invoices near the GST 180-day input-credit mark (150 days and over)" })), h("span", { class: "hint", text: s.itc_caution })) : null,
      writable() ? h("button", { type: "button", class: "btn", text: "Save", onclick: save }) : note("Read-only mode: these cannot be changed."),
      h("p", { class: "sub", style: { "margin-top": "12px" }, text: "Interest and waiver wording is a starting point for you to edit. Whether interest can be claimed depends on your agreed terms and the law; confirm with your CA." }));
  };

  /* ---------- insights: quiet customers, related firms, data health, GST 180-day ---------- */
  CO.registerView("insights", "Insights", "insights", async () => {
    let j;
    try { j = await getJSON("/api/insights"); } catch (e) { return h("div", { class: "banner", text: e.message }); }
    const hs = j.health;
    const ring = h("div", { class: "scorering" }, h("div", { class: "scorein" }, h("b", { class: "num", text: String(hs.score) }), h("small", { text: "out of 100" })));
    ring.style.setProperty("--p", String(hs.score));
    const healthCard = card("c5 r-c12", 0, h("h2", { text: "Data health", style: { "text-align": "left", "margin-bottom": "12px" } }), ring,
      h("ul", { class: "briefing", style: { "margin-top": "16px" } }, hs.items.map((i) => h("li", { class: `brief ${i.ok ? "ok" : "warn"}` }, icon(i.ok ? "check" : "alert"),
        h("span", {}, h("strong", { text: i.label }), i.ok ? "" : ` (${i.count}, −${i.penalty})`, h("br"), h("small", { class: "sub", text: i.why }),
          i.ok ? null : h("br"), i.ok ? null : h("a", { class: "linkbtn", href: `#/${i.view}`, text: i.fix }))))),
      h("p", { class: "sub", text: "Each deduction is the check's weight times the share of your data affected. Nothing is hidden." }));
    const dormant = card("c7 r-c12", 1, h("h2", { text: "Customers who have gone quiet", style: { "text-align": "left", "margin-bottom": "6px" } }),
      h("p", { class: "sub", text: "Regular buyers (three or more invoices in the last year) with no new invoice for much longer than their usual gap. Revenue at risk, found in data you already have." }),
      j.dormant.length ? simpleTable(["Customer", "Last invoice", "Quiet for", "Usual gap", "Used to bring (a month)", "Owes now"],
        j.dormant.map((r) => [custLink(r.customer), dmy(r.last_invoice), `${r.days_quiet} days`, `${r.usual_gap_days} days`, fmt0(Number(r.monthly_billing)), Number(r.open_balance) ? fmt0(Number(r.open_balance)) : "—"]), { right: [2, 3, 4, 5] })
        : h("div", { class: "empty", text: "Nobody who buys regularly has gone quiet." }));
    const gs = j.groups.groups;
    const groups = card("c12", 2, h("h2", { text: "Related firms, seen together", style: { "text-align": "left", "margin-bottom": "6px" } }),
      h("p", { class: "sub", text: "Give firms with the same promoter or family a group name (Edit details on a customer). Their exposure is added up, so a small balance in each firm does not hide a large one in total." }),
      gs.length ? h("div", { class: "groups" }, gs.map((g) => h("div", { class: "group" },
        h("div", { class: "card-head" }, h("h3", { text: g.group }), h("div", { class: "chiprow" }, g.over_limit ? h("span", { class: "chip broken", text: "Over combined limit" }) : null,
          h("span", { class: "chip muted", text: `Owes ${fmt0(Number(g.outstanding))}` }), Number(g.overdue) > 0 ? h("span", { class: "chip broken", text: `${fmt0(Number(g.overdue))} overdue` }) : null,
          g.combined_limit ? h("span", { class: "chip pending", text: `Limits ${fmt0(Number(g.combined_limit))}` }) : null)),
        simpleTable(["Firm", "Owes", "Overdue", "Oldest", "Limit"], g.members.map((m) => [custLink(m.customer), fmt0(Number(m.outstanding)), Number(m.overdue) ? fmt0(Number(m.overdue)) : "—",
          m.oldest_days ? `${m.oldest_days} days` : "—", m.credit_limit ? fmt0(Number(m.credit_limit)) : "none"]), { right: [1, 2, 3, 4] }))))
        : h("div", { class: "empty", text: j.groups.ungrouped ? `No groups yet. ${plural(j.groups.ungrouped, "customer")} can be grouped from their page.` : "No customers yet." }));
    const itc = card("c12", 3, h("h2", { text: "GST 180-day mark", style: { "text-align": "left", "margin-bottom": "6px" } }), note(j.itc.caution, "note warn"),
      !j.itc.enabled ? h("p", { class: "sub", style: { "margin-top": "12px" } }, "Switched off. Turn it on in ", h("a", { class: "plainlink", href: "#/settings", text: "Settings" }), " to flag open invoices at 150 days and over.")
        : j.itc.flags.length ? simpleTable(["Invoice", "Customer", "Days since invoice", "Open amount", ""], j.itc.flags.map((f) => [f.invoice_no, custLink(f.customer), String(f.days), fmt(f.outstanding),
          h("span", { class: `chip ${f.state === "past" ? "broken" : "muted"}`, text: f.state === "past" ? "past 180 days" : "approaching" })]), { right: [2, 3] })
          : h("div", { class: "empty", text: "No open invoice is near the 180-day mark." }));
    return h("div", {}, viewHead("Insights", "Things your data already knows: who has gone quiet, which firms belong together, and how much to trust the numbers."), h("div", { class: "grid" }, healthCard, dormant, groups, itc));
  });

  /* ---------- cheque register ---------- */
  const STATUS_LABEL = { received: "In hand", deposited: "Deposited", cleared: "Cleared", bounced: "Bounced", returned: "Returned" };
  const STATUS_CHIP = { received: "pending", deposited: "pending", cleared: "muted", bounced: "broken", returned: "muted" };
  const NEXT_LABEL = { deposited: "Mark deposited", cleared: "Mark cleared", bounced: "It bounced", returned: "Returned or cancelled" };
  CO.registerView("cheques", "Cheques", "cheque", async () => {
    let j;
    try { j = await getJSON("/api/cheques"); } catch (e) { return h("div", { class: "banner", text: e.message }); }
    const sm = j.summary;
    const kpi = (i, label, value, hint) => h("div", { class: "kpi mini glass reveal c4 r-c12", style: { "--i": i } }, h("div", { class: "label", text: label }), h("div", { class: "value plain num", text: value }), h("div", { class: "hint", text: hint }));
    const kpis = [kpi(0, "Cheques in hand", fmt0(Number(sm.in_hand)), plural(sm.in_hand_count, "cheque") + ", not yet cleared"),
      kpi(1, "Ready to deposit (3 days)", String(sm.due_soon_count), sm.due_soon_count ? `${fmt0(Number(sm.due_soon))} dated today or within three days` : "None due yet"),
      kpi(2, "Bounced", String(sm.bounced_count), sm.bounced_count ? `${fmt0(Number(sm.bounced))} to follow up` : "No bounced cheques")];
    let filter = "open";
    const body = h("div"), seg = h("div", { class: "seg" });
    const show = (c) => (filter === "all" ? true : filter === "open" ? ["received", "deposited", "bounced"].includes(c.status) : c.status === filter);
    async function step(c, status) {
      const run = async (noteText) => {
        await post("/api/cheque/status", { id: c.id, status, note: noteText || "", actor: actor() });
        toast(`${STATUS_LABEL[status]}.`);
        await reload();
        // the window that asked for the note closes itself when this returns, so open the follow-up just after
        if (status === "bounced" && writable()) setTimeout(() => CO.openFollowup(c.customer, { outcome: `Cheque ${c.cheque_no} bounced` }), 350);
      };
      if (status === "bounced" || status === "returned") {
        const why = h("input", { id: "ck-why", placeholder: status === "bounced" ? "Reason, e.g. insufficient funds" : "Why it came back", maxlength: "200" });
        CO.openModal({ title: `${NEXT_LABEL[status]}: cheque ${c.cheque_no}`, intro: `${c.customer}, ${fmt(c.amount)}`, fields: [field("Note (optional)", why)], submit: "Save", onSubmit: async () => { await run(why.value); return "Saved"; } });
      } else { try { await run(""); } catch (e) { toast(e.message); } }
    }
    function draw() {
      seg.replaceChildren(...[["open", "Open"], ["all", "All"], ["bounced", "Bounced"], ["cleared", "Cleared"]].map(([id, t]) => h("button", { type: "button", "aria-pressed": String(filter === id), text: t, onclick: () => { filter = id; draw(); } })));
      const rows = j.cheques.filter(show);
      body.replaceChildren(rows.length ? h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Date", "Customer", "Cheque", "Amount", "Status", "For invoices", ""].map((t, i) => h("th", { scope: "col", class: i === 3 ? "r" : "", text: t })))),
        h("tbody", {}, rows.map((c) => h("tr", {}, h("td", {}, dmy(c.cheque_date), h("br"), h("small", { class: "sub", text: c.days_to_date === 0 ? "today" : c.days_to_date > 0 ? `in ${plural(c.days_to_date, "day")}` : `${plural(-c.days_to_date, "day")} ago` })),
          h("td", {}, custLink(c.customer)), h("td", {}, c.cheque_no, h("br"), h("small", { class: "sub", text: c.bank || "" })), h("td", { class: "r num", text: fmt(c.amount) }),
          h("td", {}, h("span", { class: `chip ${STATUS_CHIP[c.status]}`, text: STATUS_LABEL[c.status] }), c.note ? h("div", { class: "sub", text: c.note }) : null),
          h("td", { text: c.invoice_nos || "—" }), h("td", {}, h("div", { class: "ckactions" }, writable() ? c.next.map((n) => h("button", { type: "button", class: "btn small " + (n === "bounced" ? "bad" : "ghost"), text: NEXT_LABEL[n], onclick: () => step(c, n) })) : null)))))))
        : h("div", { class: "empty", text: filter === "open" ? "No open cheques. Add one when a customer hands you a post-dated cheque." : "Nothing here." }));
    }
    const f = { cust: h("input", { id: "ck-cust", list: "ck-names", placeholder: "Customer", autocomplete: "off" }), no: h("input", { id: "ck-no", placeholder: "Cheque number", maxlength: "40" }), bank: h("input", { id: "ck-bank", placeholder: "Bank", maxlength: "60" }),
      amt: h("input", { id: "ck-amt", placeholder: "Amount", inputmode: "decimal" }), date: h("input", { id: "ck-date", type: "date", value: j.as_of }), inv: h("input", { id: "ck-inv", placeholder: "e.g. INV-1042, INV-1043", maxlength: "200" }) };
    const form = h("form", { class: "setgrid", novalidate: true, onsubmit: async (e) => {
      e.preventDefault();
      try { await post("/api/cheque/add", { customer: f.cust.value.trim(), cheque_no: f.no.value, bank: f.bank.value, amount: cleanAmount(f.amt.value), cheque_date: f.date.value, invoice_nos: f.inv.value, actor: actor() }); toast("Cheque added."); await reload(); }
      catch (ex) { toast(ex.message); }
    } },
      h("div", { class: "field" }, h("label", { for: "ck-cust", text: "Customer" }), f.cust, h("datalist", { id: "ck-names" }, j.customers.map((n) => h("option", { value: n })))),
      h("div", { class: "field" }, h("label", { for: "ck-no", text: "Cheque number" }), f.no), h("div", { class: "field" }, h("label", { for: "ck-bank", text: "Bank" }), f.bank),
      h("div", { class: "field" }, h("label", { for: "ck-amt", text: "Amount (₹)" }), f.amt), h("div", { class: "field" }, h("label", { for: "ck-date", text: "Date on the cheque" }), f.date),
      h("div", { class: "field" }, h("label", { for: "ck-inv", text: "For invoices (optional)" }), f.inv), h("div", { class: "field" }, h("label", { for: "ck-go", class: "sr-only", text: "Add" }), h("button", { id: "ck-go", type: "submit", class: "btn", text: "Add cheque" })));
    draw();
    return h("div", {}, viewHead("Post-dated cheques", "Track each cheque from the day you receive it to the day it clears, or bounces."),
      h("div", { class: "grid" }, ...kpis, writable() ? card("c12", 3, h("h2", { text: "Add a cheque", style: { "text-align": "left", "margin-bottom": "8px" } }), form) : null,
        card("c12", 4, h("div", { class: "card-head" }, h("h2", { text: "Register" }), seg), body,
          note("A cheque in hand is not payment. It does not reduce what the customer owes until it clears and the invoice is updated, by your next import or by confirming it as paid. Dated cheques do count in the cash calendar."))));
  });

  /* ---------- TDS register ---------- */
  CO.registerView("tds", "TDS register", "tds", async (_d, fyParam) => {
    let j;
    try { j = await getJSON("/api/tds", fyParam ? { fy: fyParam } : {}); } catch (e) { return h("div", { class: "banner", text: e.message }); }
    const sel = h("select", { id: "tds-fy", "aria-label": "Financial year", onchange: (e) => { location.hash = "#/tds/" + e.target.value; } }, j.years.map((y) => h("option", { value: y, text: `FY ${y}`, selected: y === j.fy })));
    const quarters = j.quarters.map((q) => h("div", { class: "kpi mini glass reveal c3 r-c6", style: { "--i": q.quarter } },
      h("div", { class: "label", text: `Q${q.quarter} · ${["Apr–Jun", "Jul–Sep", "Oct–Dec", "Jan–Mar"][q.quarter - 1]}` }), h("div", { class: "value plain num", text: fmt0(Number(q.total)) }),
      h("div", { class: "hint", text: q.count ? (Number(q.not_in_26as) > 0 ? `${fmt0(Number(q.not_in_26as))} not yet seen in 26AS` : "all seen in 26AS") : "nothing recorded" })));
    const tick = async (e, id) => { try { await post("/api/tds/26as", { id, seen: e.target.checked, actor: actor() }); await reload(); } catch (ex) { e.target.checked = !e.target.checked; toast(ex.message); } };
    const voidIt = (en) => {
      const why = h("input", { id: "tv-why", maxlength: "200" });
      CO.openModal({ title: "Void this TDS entry?", intro: `${en.customer}, ${fmt(en.tds)}. It stays in the log but leaves the register.`, fields: [field("Why (optional)", why)], submit: "Void entry", danger: true,
        onSubmit: async () => { await post("/api/tds/void", { id: en.id, note: why.value, actor: actor() }); return "Voided"; } });
    };
    const table = j.entries.length ? h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Date", "Quarter", "Customer", "Invoice", "Gross", "TDS deducted", "In 26AS", ""].map((t, i) => h("th", { scope: "col", class: i === 4 || i === 5 ? "r" : "", text: t })))),
      h("tbody", {}, j.entries.map((en) => h("tr", {}, h("td", { text: dmy(en.deducted_on) }), h("td", { text: `Q${en.quarter}` }), h("td", {}, custLink(en.customer), en.note ? h("div", { class: "sub", text: en.note }) : null), h("td", { text: en.invoice_no || "—" }),
        h("td", { class: "r num", text: en.gross ? fmt(en.gross) : "—" }), h("td", { class: "r num", text: fmt(en.tds) }),
        h("td", {}, writable() ? h("input", { type: "checkbox", checked: en.in_26as, "aria-label": `Seen in Form 26AS: ${en.customer}, ${fmt(en.tds)}`, onchange: (e) => tick(e, en.id) }) : h("span", { text: en.in_26as ? "yes" : "no" })),
        h("td", {}, writable() ? h("button", { type: "button", class: "btn small ghost", text: "Void", onclick: () => voidIt(en) }) : null))))))
      : h("div", { class: "empty", text: `No TDS recorded for FY ${j.fy} yet.` });
    const f = { cust: h("input", { id: "td-cust", list: "td-names", placeholder: "Customer", autocomplete: "off" }), date: h("input", { id: "td-date", type: "date", value: j.default_date }), tds: h("input", { id: "td-tds", placeholder: "TDS amount", inputmode: "decimal" }),
      gross: h("input", { id: "td-gross", placeholder: "Gross payment (optional)", inputmode: "decimal" }), inv: h("input", { id: "td-inv", placeholder: "Invoice (optional)", maxlength: "60" }), note: h("input", { id: "td-note", placeholder: "Note (optional)", maxlength: "200" }) };
    const form = h("form", { class: "setgrid", novalidate: true, onsubmit: async (e) => {
      e.preventDefault();
      try { await post("/api/tds/add", { customer: f.cust.value.trim(), deducted_on: f.date.value, tds: cleanAmount(f.tds.value), gross: cleanAmount(f.gross.value), invoice_no: f.inv.value, note: f.note.value, actor: actor() }); toast("TDS recorded."); await reload(); }
      catch (ex) { toast(ex.message); }
    } },
      h("div", { class: "field" }, h("label", { for: "td-cust", text: "Customer" }), f.cust, h("datalist", { id: "td-names" }, j.customers.map((n) => h("option", { value: n })))),
      h("div", { class: "field" }, h("label", { for: "td-date", text: "Date deducted" }), f.date), h("div", { class: "field" }, h("label", { for: "td-tds", text: "TDS deducted (₹)" }), f.tds),
      h("div", { class: "field" }, h("label", { for: "td-gross", text: "Gross payment (₹)" }), f.gross), h("div", { class: "field" }, h("label", { for: "td-inv", text: "Invoice" }), f.inv),
      h("div", { class: "field" }, h("label", { for: "td-note", text: "Note" }), f.note), h("div", { class: "field" }, h("label", { for: "td-go", class: "sr-only", text: "Record" }), h("button", { id: "td-go", type: "submit", class: "btn", text: "Record TDS" })));
    return h("div", {}, viewHead("TDS register", "Tax your customers deducted before paying you, by quarter, so you can match it against Form 26AS and claim every rupee.",
      h("div", { class: "head-actions" }, sel, h("a", { class: "btn small ghost", href: "/tds.csv?" + new URLSearchParams({ fy: j.fy }), download: "" }, icon("download"), "Download CSV"))),
      h("div", { class: "grid" }, ...quarters, writable() ? card("c12", 5, h("h2", { text: "Record TDS deducted", style: { "text-align": "left", "margin-bottom": "8px" } }), form,
        h("p", { class: "sub", text: "Where to find it: the shortfall between what an invoice said and what the bank received (see Payments), or the customer's remittance advice." })) : null,
        card("c12", 6, h("div", { class: "card-head" }, h("h2", { text: `FY ${j.fy}` }), h("span", { class: "chip muted", text: `Total ${fmt(j.total)}` })), table,
          note("Each quarter, check Form 26AS on the income-tax portal and tick the entries you find there. Entries left unticked are the ones to chase the customer for (a TDS certificate, Form 16A). This is a record for you and your CA, not tax advice."))));
  });
})();
