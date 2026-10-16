/* CollectOS dashboard: the extras that make the weekly cycle easier and give owners something beyond a reminder list:
   morning brief, statement of account, interest and waiver offer, "can I ship this order?", cash calendar and the
   settings behind them. Loaded after features.js. Text from the server is untrusted and only inserted with textContent. */
(() => {
  "use strict";
  const CO = window.CO;
  const { h, icon, fmt, fmt0, dmy, dm, plural, state, store, toast, post, getJSON, load, writable, actor, custLink, copy, viewHead, dlg } = CO;
  const { card, note, spinner, back, simpleTable } = CO.ui;

  const num = (s) => Number(String(s).replace(/[₹,\s]/g, ""));
  const cleanAmount = (s) => String(s).replace(/[₹,\s]/g, "").replace(/^rs\.?/i, "");

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
    if (!d.configured) {
      dlg.replaceChildren(h("div", { class: "modal-inner" }, h("h2", { id: "modalTitle", text: "Interest and waiver offer" }), note(d.note),
        h("p", { class: "sub", text: "Enter the interest rate from your own invoice or purchase-order terms. CollectOS does not know what was agreed with your customers." }),
        h("div", { class: "modal-actions" }, h("button", { type: "button", class: "btn ghost", text: "Close", onclick: () => dlg.close() }),
          h("a", { class: "btn", href: "#/settings", text: "Open settings", onclick: () => dlg.close() }))));
      return;
    }
    const days = h("input", { id: "w-days", type: "number", min: "1", max: "30", value: String(d.waiver_days), "aria-label": "Days the offer stays open" });
    const summary = h("div", { class: "stmt" },
      h("div", { class: "note warn", text: d.terms_note }),
      d.msmed_note ? h("p", { class: "sub", text: d.msmed_note }) : null,
      d.rows.length ? simpleTable(["Invoice", "Overdue amount", "Days past due", "Interest"], d.rows.map((r) => [r.invoice_no, fmt(r.outstanding), String(r.days_overdue), fmt(r.interest)]), { right: [1, 2, 3] })
        : note("No interest has accrued yet (everything is within the grace days, not overdue, or in dispute)."),
      d.rows.length ? h("p", { class: "sub", text: `Simple interest at ${d.rate}% a year${d.grace_days ? `, after ${d.grace_days} grace days` : ""}: ${fmt(d.interest)} on ${fmt(d.overdue)} overdue.` }) : null,
      d.disputed_left_out.length ? h("p", { class: "sub", text: `Left out because they are in dispute: ${d.disputed_left_out.join(", ")}.` }) : null,
      h("div", { class: "field inline" }, h("label", { for: "w-days", text: "Offer stays open (days)" }), days,
        h("button", { type: "button", class: "btn small ghost", text: "Update draft", onclick: () => openInterest(customer, Number(days.value) || 7) })),
      h("p", { class: "sub", text: "Offering a waiver is your commercial decision. The draft promises it only if the full overdue amount arrives by the date shown." }));
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
        h("span", { class: "chip muted", text: i.kind === "promise" ? "their promise" : "due by terms" }), h("span", { class: `chip ${i.firm ? "pending" : "broken"}`, text: i.firm ? "firm" : "shaky" }),
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
    ];
    const save = async (e) => {
      e.currentTarget.disabled = true;
      try {
        for (const r of rows) {
          const raw = r.el.value.trim();
          const value = r.key === "interest_rate" || r.key === "monthly_outgoings" ? (cleanAmount(raw) || "0") : r.key === "interest_grace_days" ? (raw || "0") : raw;
          const shown = { interest_rate: s.interest_rate, monthly_outgoings: s.monthly_outgoings }[r.key];
          const same = shown !== undefined ? Number(value) === Number(shown) : value === s[r.key];
          if (!same) await post("/api/setting", { key: r.key, value, actor: actor() });
        }
        toast("Saved"); await load(true);
      } catch (ex) { toast(ex.message); e.currentTarget.disabled = false; }
    };
    return card("c12", 3, h("h2", { text: "Payments, interest and cash planning", style: { "text-align": "left", "margin-bottom": "6px" } }),
      h("p", { class: "sub", text: "These stay in your local database. UPI details only appear in messages you choose to send." }),
      h("div", { class: "setgrid" }, rows.map((r) => h("div", { class: "field" }, h("label", { for: `x-${r.id}`, text: r.label }), r.el, r.hint ? h("span", { class: "hint", text: r.hint }) : null))),
      writable() ? h("button", { type: "button", class: "btn", text: "Save", onclick: save }) : note("Read-only mode: these cannot be changed."),
      h("p", { class: "sub", style: { "margin-top": "12px" }, text: "Interest and waiver wording is a starting point for you to edit. Whether interest can be claimed depends on your agreed terms and the law; confirm with your CA." }));
  };
})();
