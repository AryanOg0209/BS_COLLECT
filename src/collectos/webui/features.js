/* CollectOS dashboard: the screens built on top of the core (customer 360, sending reminders, disputes,
   credit, import, bank-credit matching, call sheet, activity). Loaded after app.js, which exposes window.CO.
   As everywhere: text from the server is untrusted and only ever inserted with textContent. */
(() => {
  "use strict";
  const CO = window.CO;
  const { h, icon, fmt, fmt0, compact, dmy, dm, pct, plural, state, store, toast, openModal, field, post, upload, getJSON, load, go, writable,
    actor, custLink, custHref, copy, viewHead, todayISO, dlg } = CO;

  /* ---------- small shared pieces ---------- */
  const STAGE_NAMES = { 1: "Friendly reminder", 2: "Firm follow-up", 3: "Owner call", 4: "Review credit terms" };
  const stageChip = (stage, label, why) => (stage ? h("span", { class: `chip stagechip s${stage}`, title: why || "", text: `${stage} · ${label || STAGE_NAMES[stage]}` }) : h("span", { class: "chip muted", text: "Up to date" }));
  const confChip = (c) => h("span", { class: `chip conf ${c}`, text: { high: "High match", medium: "Possible", low: "Weak clue", none: "No match" }[c] });
  const when = (ts) => { try { return new Date(ts).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }); } catch { return ts; } };
  const rupees = (paise) => fmt((paise / 100).toFixed(2));
  function simpleTable(heads, rows, { right = [], cls = "" } = {}) {
    if (!rows.length) return null;
    return h("div", { class: "tablewrap" }, h("table", { class: cls }, h("thead", {}, h("tr", {}, heads.map((t, i) => h("th", { scope: "col", class: right.includes(i) ? "r" : "", text: t })))),
      h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: right.includes(i) ? "r num" : "" }, c)))))));
  }
  const card = (cls, i, ...kids) => h("section", { class: `card glass reveal ${cls}`, style: { "--i": i } }, ...kids);
  const note = (text, cls = "note") => h("div", { class: cls, text });
  const spinner = (text) => h("div", { class: "empty", text });
  const readOnlyNote = () => note("This dashboard is running in read-only mode, so nothing here can be changed.");
  const back = (href, text) => h("a", { class: "backlink", href }, icon("chevron-left"), text);

  /* ---------- editing a customer's details ---------- */
  function openEditCustomer(detail, after) {
    if (!writable()) return;
    const p = detail.profile;
    const person = h("input", { id: "e-person", value: p.person, placeholder: "e.g. Mr Gupta (accounts)" });
    const phone = h("input", { id: "e-phone", value: p.phone, placeholder: "e.g. 98765 43210", inputmode: "tel" });
    const email = h("input", { id: "e-email", value: p.email, placeholder: "accounts@example.com", type: "email" });
    const pref = h("select", { id: "e-pref" }, ...[["", "No preference"], ["call", "Phone call"], ["whatsapp", "WhatsApp"], ["email", "Email"], ["visit", "Visit"]]
      .map(([v, t]) => h("option", { value: v, text: t, selected: p.preferred === v })));
    const limit = h("input", { id: "e-limit", value: p.credit_limit ? String(Number(p.credit_limit)) : "", placeholder: "Leave empty for no limit", inputmode: "decimal" });
    const dnc = h("input", { type: "checkbox", id: "e-dnc", checked: p.do_not_contact });
    const notes = h("textarea", { id: "e-notes", placeholder: "Anything worth remembering (best time to call, who decides...)" }); notes.value = p.notes;
    openModal({
      title: `Details for ${detail.customer}`, intro: "Saved on this computer only. A phone number lets you open WhatsApp or a call with one click.",
      fields: [field("Contact person", person), h("div", { class: "promise-box", style: { background: "none", border: "0", padding: "0" } }, field("Phone", phone), field("Email", email)),
        field("Preferred way to reach them", pref), field("Credit limit (₹)", limit, "Used to flag when what they owe goes over your limit."),
        h("label", { class: "checkrow" }, dnc, h("span", { text: "Do not contact (handle this customer outside the daily list)" })), field("Notes", notes)],
      submit: "Save details",
      onSubmit: async () => {
        await post("/api/customer/update", { customer: detail.customer, contact_person: person.value, phone: phone.value, email: email.value, preferred_channel: pref.value,
          credit_limit: limit.value, do_not_contact: dnc.checked, notes: notes.value, actor: actor() });
        if (after) setTimeout(after, 50);
        return "Details saved.";
      },
    });
  }

  /* ---------- disputes ---------- */
  const CATEGORIES = [["price", "Price or rate"], ["quantity", "Quantity"], ["quality", "Quality"], ["delivery", "Delivery or proof of delivery"], ["tax", "GST or tax"], ["duplicate", "Duplicate invoice"], ["other", "Other"]];
  const categoryName = (c) => (CATEGORIES.find((x) => x[0] === c) || [c, c])[1];
  function openDispute(invoiceNo = "", customer = "") {
    if (!writable()) return;
    const names = (state.data ? state.data.customers : []).map((c) => c.customer);
    const cust = h("input", { id: "d-cust", list: "d-cust-list", value: customer, placeholder: "Customer name", autocomplete: "off" });
    const inv = h("input", { id: "d-inv", value: invoiceNo, placeholder: "e.g. INV-1042" });
    const cat = h("select", { id: "d-cat" }, ...CATEGORIES.map(([v, t]) => h("option", { value: v, text: t })));
    const owner = h("input", { id: "d-owner", placeholder: "Who is sorting this out?" });
    const text = h("textarea", { id: "d-note", placeholder: "What does the customer say is wrong?" });
    openModal({
      title: "Mark an invoice as disputed", intro: "A disputed invoice is kept out of the daily chase list so nobody asks for money the customer has questioned. It stays in the totals.",
      fields: [h("datalist", { id: "d-cust-list" }, names.map((n) => h("option", { value: n }))), field("Customer", cust), field("Invoice number", inv), field("What is the dispute about?", cat), field("Owner", owner), field("Details", text)],
      submit: "Mark as disputed",
      onSubmit: async () => { await post("/api/dispute/open", { invoice_no: inv.value, customer: cust.value, category: cat.value, owner: owner.value, note: text.value, actor: actor() }); return "Marked as disputed."; },
    });
  }
  function openResolveDispute(d) {
    if (!writable()) return;
    const text = h("textarea", { id: "dr-note", placeholder: "How was it settled? (credit note, corrected rate, delivery proof...)" });
    openModal({
      title: "Resolve this dispute?", intro: `${d.invoice_no} for ${d.customer} (${categoryName(d.category)}). Once resolved, the invoice goes back into the daily chase list if it is still unpaid.`,
      fields: [field("Resolution", text)], submit: "Resolve dispute",
      onSubmit: async () => { await post("/api/dispute/resolve", { id: d.id, note: text.value, actor: actor() }); return "Dispute resolved."; },
    });
  }

  /* ---------- sending a reminder: the person always presses send ---------- */
  async function openSend(customer, startLang) {
    const lang0 = startLang || store.get("lang", "en");
    dlg.classList.add("wide");
    dlg.replaceChildren(h("div", { class: "modal-inner" }, spinner("Preparing the draft…")));
    if (!dlg.open) dlg.showModal();
    let lang = lang0, channel = "whatsapp", draft;
    const sender = store.get("sender", ""), company = store.get("company", "");
    const text = h("textarea", { id: "s-text", class: "draft-edit", "aria-label": "Message text" });
    const subject = h("input", { id: "s-subject", "aria-label": "Email subject" });
    const body = h("div"), actions = h("div", { class: "send-actions" }), confirm = h("div"), langSeg = h("div", { class: "seg" }), chanSeg = h("div", { class: "seg" });

    const waUrl = () => (draft.contact.phone ? `https://wa.me/${draft.contact.phone.replace("+", "")}?text=${encodeURIComponent(text.value)}` : null);
    const mailUrl = () => (draft.contact.email ? `mailto:${draft.contact.email}?subject=${encodeURIComponent(subject.value)}&body=${encodeURIComponent(text.value)}` : null);

    async function fetchDraft() {
      try {
        draft = await getJSON("/api/draft", { customer, sender, company, language: lang });
      } catch (e) { dlg.replaceChildren(h("div", { class: "modal-inner" }, h("div", { class: "form-error", text: e.message }), h("div", { class: "modal-actions" }, h("button", { type: "button", class: "btn ghost", text: "Close", onclick: () => dlg.close() })))); return false; }
      return true;
    }
    function fill() { text.value = channel === "whatsapp" ? draft.whatsapp : draft.email; subject.value = draft.subject; subject.hidden = channel !== "email"; }
    function sentPrompt(via) {
      confirm.replaceChildren(h("div", { class: "note" }, h("strong", { text: "Did you send it? " }), "If so, record it so the list knows this customer was contacted.",
        h("div", { class: "modal-actions", style: { "margin-top": "10px" } },
          h("button", { type: "button", class: "btn ghost", text: "Not yet", onclick: () => confirm.replaceChildren() }),
          h("button", { type: "button", class: "btn", text: "Yes, log it", onclick: async (e) => {
            e.currentTarget.disabled = true;
            try { await post("/api/followup", { customer, channel: via, outcome: `Sent reminder (${via === "whatsapp" ? "WhatsApp" : via === "email" ? "email" : "call"})`, actor: actor() }); dlg.close(); toast("Logged as contacted."); await load(); }
            catch (ex) { toast(ex.message); e.currentTarget.disabled = false; }
          } }))));
    }
    function draw() {
      const c = draft.contact;
      langSeg.replaceChildren(...[["en", "English"], ["hinglish", "Hinglish"]].map(([id, t]) => h("button", { type: "button", "aria-pressed": String(lang === id), text: t,
        onclick: async () => { lang = id; if (await fetchDraft()) { fill(); draw(); } } })));
      chanSeg.replaceChildren(...[["whatsapp", "WhatsApp"], ["email", "Email"]].map(([id, t]) => h("button", { type: "button", "aria-pressed": String(channel === id), text: t,
        onclick: () => { channel = id; fill(); draw(); } })));
      const links = [];
      const blocked = c.do_not_contact;
      if (channel === "whatsapp") links.push(c.phone && !blocked ? h("a", { class: "btn", href: waUrl(), target: "_blank", rel: "noopener noreferrer", onclick: (e) => { e.currentTarget.href = waUrl(); sentPrompt("whatsapp"); } }, icon("chat"), "Open WhatsApp")
        : h("button", { type: "button", class: "btn", disabled: true }, icon("chat"), "Open WhatsApp"));
      else links.push(c.email && !blocked ? h("a", { class: "btn", href: mailUrl(), onclick: (e) => { e.currentTarget.href = mailUrl(); sentPrompt("email"); } }, icon("reminders"), "Open email")
        : h("button", { type: "button", class: "btn", disabled: true }, icon("reminders"), "Open email"));
      if (c.phone && !blocked) links.push(h("a", { class: "btn ghost", href: `tel:${c.phone}`, onclick: () => sentPrompt("call") }, icon("phone"), `Call ${c.phone}`));
      links.push(h("button", { type: "button", class: "btn ghost", onclick: () => copy(channel === "email" ? `Subject: ${subject.value}\n\n${text.value}` : text.value) }, icon("copy"), "Copy"));
      actions.replaceChildren(...links);
      body.replaceChildren(...[
        blocked ? h("div", { class: "form-error", text: "This customer is marked do-not-contact. Handle them outside the daily list." }) : null,
        !blocked && channel === "whatsapp" && !c.phone ? note("No phone number saved yet, so WhatsApp cannot open. Add one below.") : null,
        !blocked && channel === "email" && !c.email ? note("No email saved yet, so the mail app cannot open. Add one below.") : null,
        channel === "email" ? h("div", { class: "field" }, h("label", { for: "s-subject", text: "Subject" }), subject) : null,
        h("div", { class: "field" }, h("label", { for: "s-text", text: "Message (you can edit it before sending)" }), text),
        h("p", { class: "sub", text: "This opens your own WhatsApp or mail app with the text filled in. You read it and press send; CollectOS never sends anything itself." })].filter(Boolean));
    }

    if (!(await fetchDraft())) return;
    fill();
    const head = h("div", { class: "send-head" }, h("div", {}, h("h2", { id: "modalTitle", text: `Reminder for ${customer}` }),
      h("p", { class: "sub", text: [draft.contact.person, draft.contact.phone, draft.contact.email].filter(Boolean).join(" · ") || "No contact details saved yet" })),
      h("div", { class: "send-segs" }, langSeg, chanSeg));
    const foot = h("div", { class: "modal-actions", style: { "justify-content": "space-between", "flex-wrap": "wrap" } },
      h("div", { style: { display: "flex", gap: "8px", "flex-wrap": "wrap" } },
        writable() ? h("button", { type: "button", class: "btn ghost", onclick: async () => { const d = await getJSON("/api/customer", { name: customer }); openEditCustomer(d, () => openSend(customer, lang)); } }, icon("edit"), "Contact details") : null),
      h("div", { style: { display: "flex", gap: "8px", "flex-wrap": "wrap" } }, actions, h("button", { type: "button", class: "btn ghost", text: "Close", onclick: () => dlg.close() })));
    dlg.replaceChildren(h("div", { class: "modal-inner" }, head, body, confirm, foot));
    draw();
  }
  CO.openSend = openSend; CO.openEditCustomer = openEditCustomer; CO.openDispute = openDispute;

  /* ---------- dashboard additions: mini KPIs, daily briefing, data health ---------- */
  CO.extra.dashboardTop = (d) => {
    const k = d.kpi;
    const mini = (i, label, value, hint, target) => {
      const el = h("div", { class: "kpi mini glass reveal c3 r-c6", style: { "--i": i } }, h("div", { class: "label", text: label }), h("div", { class: "value plain num", text: value }), h("div", { class: "hint", text: hint }));
      if (target) { el.classList.add("clickable"); el.tabIndex = 0; el.addEventListener("click", () => go(target)); el.addEventListener("keydown", (e) => { if (e.key === "Enter") go(target); }); }
      CO.tilt(el, 4);
      return el;
    };
    const minis = [
      mini(4, "Contacted in the last 7 days", k.coverage_7d === null ? "—" : pct(k.coverage_7d), k.coverage_7d === null ? "Nothing overdue" : `${k.contacted_7d} of ${k.customers_overdue} overdue customers`, "queue"),
      mini(5, "Average age of overdue money", k.avg_days_overdue === null ? "—" : `${Math.round(Number(k.avg_days_overdue))} days`, "Weighted by rupee amount", "customers"),
      mini(6, "Promised in the next 7 days", k.promised_7d_count ? plural(k.promised_7d_count, "promise") : "None", Number(k.promised_7d_amount) > 0 ? `${fmt0(Number(k.promised_7d_amount))} promised by customers` : "Customers' own promises, not a forecast", "promises"),
      mini(7, "In dispute", String(k.disputed_count), k.disputed_count ? `${fmt0(Number(k.disputed_overdue))} overdue, kept out of the chase list` : "No invoices in dispute", "disputes"),
    ];
    const lines = d.briefing.map((b) => h("li", { class: `brief ${b.level}` }, icon(b.level === "warn" ? "alert" : b.level === "ok" ? "check" : "chat"), h("span", { text: b.text }),
      b.view && b.view !== "health" ? h("a", { class: "linkbtn", href: `#/${b.view}`, text: "Open" }) : (b.view === "health" ? h("a", { class: "linkbtn", href: "#/import", text: "Import" }) : null)));
    const SHOWN = 6; let expanded = false;
    const list = h("ul", { class: "briefing" }), more = h("button", { type: "button", class: "linkbtn", style: { "margin-top": "10px" } });
    const paintBriefing = () => {
      list.replaceChildren(...(expanded ? lines : lines.slice(0, SHOWN)));
      more.hidden = lines.length <= SHOWN; more.textContent = expanded ? "Show fewer" : `Show ${lines.length - SHOWN} more`;
    };
    more.addEventListener("click", () => { expanded = !expanded; paintBriefing(); });
    paintBriefing();
    const briefing = card("c8 r-c12", 8, h("div", { class: "card-head" }, h("h2", { text: "Today's briefing" }), h("span", { class: "sub", text: dmy(d.as_of) })), list, more);
    const hl = d.health, dupes = hl.near_duplicate_names;
    const items = [
      hl.no_due_date.count ? ["warn", `${plural(hl.no_due_date.count, "invoice")} without a due date (${fmt0(Number(hl.no_due_date.amount))}). They cannot be aged.`] : null,
      dupes.length ? ["warn", `Possible duplicate customer names: ${dupes.slice(0, 2).map((g) => g.join(" / ")).join("; ")}${dupes.length > 2 ? ` and ${dupes.length - 2} more` : ""}.`] : null,
      hl.overpaid ? ["warn", `${plural(hl.overpaid, "invoice")} paid more than the amount billed.`] : null,
    ].filter(Boolean);
    const health = card("c4 r-c12", 9, h("div", { class: "card-head" }, h("h2", { text: "Data health" })),
      items.length ? h("ul", { class: "briefing" }, items.map(([lvl, t]) => h("li", { class: `brief ${lvl}` }, icon(lvl === "warn" ? "alert" : "chat"), h("span", { text: t }))))
        : h("div", { class: "empty", text: "Everything looks consistent." }),
      h("a", { class: "btn small ghost", href: "#/import", style: { "margin-top": "12px" } }, icon("import"), "Import a fresh export"));
    return [...minis, briefing, health];
  };

  /* ---------- customer 360 ---------- */
  CO.registerView("customer", "Customer", null, async (data, name) => {
    if (!name) return h("div", { class: "empty", text: "Choose a customer from the list." });
    let d;
    try { d = await getJSON("/api/customer", { name }); } catch (e) { return h("div", {}, back("#/customers", "Customers"), h("div", { class: "banner", text: e.message })); }
    const root = h("div");
    const p = d.profile, s = d.summary, canWrite = writable();
    const refresh = () => load();
    const actions = h("div", { class: "head-actions" },
      h("button", { type: "button", class: "btn small", onclick: () => openSend(d.customer) }, icon("chat"), "Send reminder"),
      canWrite ? h("button", { type: "button", class: "btn small ghost", onclick: () => CO.openFollowup(d.customer) }, icon("phone"), "Log follow-up") : null,
      canWrite ? h("button", { type: "button", class: "btn small ghost", onclick: () => openEditCustomer(d, refresh) }, icon("edit"), "Edit details") : null);
    root.append(back("#/customers", "Customers"), viewHead(d.customer, `As of ${dmy(d.as_of)}`, actions));
    const chips = h("div", { class: "chiprow" }, stageChip(d.stage, d.stage_label, d.stage_why), p.do_not_contact ? h("span", { class: "chip muted", text: "Do not contact" }) : null,
      s.over_limit ? h("span", { class: "chip broken", text: "Over credit limit" }) : null, d.waiting_reason ? h("span", { class: "chip muted", text: d.waiting_reason }) : null);
    const util = s.utilisation === null ? null : Math.min(1, Number(s.utilisation));

    const profile = card("c4 r-c12", 0, h("h2", { text: "Who and how", style: { "text-align": "left", "margin-bottom": "12px" } }), chips,
      h("dl", { class: "facts" },
        h("dt", { text: "Contact" }), h("dd", { text: p.person || "Not saved" }),
        h("dt", { text: "Phone" }), h("dd", {}, p.phone ? h("a", { class: "plainlink", href: `tel:${p.phone}`, text: p.phone }) : "Not saved"),
        h("dt", { text: "Email" }), h("dd", { text: p.email || "Not saved" }),
        h("dt", { text: "Prefers" }), h("dd", { text: { "": "No preference", call: "Phone call", whatsapp: "WhatsApp", email: "Email", visit: "Visit" }[p.preferred] }),
        h("dt", { text: "Credit limit" }), h("dd", { text: p.credit_limit ? fmt(p.credit_limit) : "None set" })),
      util === null ? null : h("div", { class: "util" }, h("div", { class: "utilbar" + (s.over_limit ? " over" : "") }, h("i", { style: { "--w": util * 100 + "%" } })),
        h("span", { class: "sub", text: `${fmt0(Number(s.outstanding))} of ${fmt0(Number(p.credit_limit))} used (${Math.round(Number(s.utilisation) * 100)}%)` })),
      d.stage ? h("p", { class: "sub", style: { "margin-top": "12px" }, text: `${d.stage_label}: ${d.stage_why}. This is a suggestion for you; nothing happens automatically.` }) : null,
      p.notes ? h("div", { class: "note", style: { "margin-top": "12px" }, text: p.notes }) : null);

    const rel = d.reliability, relTotal = rel.kept + rel.broken;
    const stat = (label, value, hint) => h("div", { class: "kpi mini glass" }, h("div", { class: "label", text: label }), h("div", { class: "value plain num", text: value }), h("div", { class: "hint", text: hint }));
    const stats = h("div", { class: "c8 r-c12 statgrid" },
      stat("Outstanding", fmt0(Number(s.outstanding)), plural(s.open_invoices, "open invoice")),
      stat("Overdue", fmt0(Number(s.overdue)), s.oldest_days ? `oldest ${s.oldest_days} days` : "nothing overdue"),
      stat("Promise record", relTotal ? `${rel.kept} of ${relTotal} kept` : "No history", relTotal ? (rel.broken ? `${rel.broken} broken` : "all kept so far") : "Log a promise to start one"),
      stat("In dispute", String(d.disputes.filter((x) => x.status === "open").length), "open disputes"));
    root.append(h("div", { class: "grid" }, profile, stats));

    const grid = h("div", { class: "grid", style: { "margin-top": "20px" } });
    if (d.suggestions.length) {
      grid.append(card("c12", 1, ...d.suggestions.map((sg) => {
        const pr = d.promises.find((x) => x.id === sg.promise_id);
        return h("div", { class: "note suggest" }, icon("check"), h("span", { text: `${fmt(sg.observed)} of payments arrived since the promise for ${dmy(pr.date)}${pr.amount ? " of " + fmt(pr.amount) : ""}. ${sg.kind === "looks_kept" ? "It looks kept." : "It looks partly kept."}` }),
          canWrite ? h("button", { type: "button", class: "btn small good", text: "Mark kept", onclick: () => CO.openResolve({ id: pr.id, customer: d.customer, date: pr.date, amount: pr.amount }, "kept") }) : null);
      })));
    }
    const invRows = d.invoices.map((i) => [
      h("span", {}, i.invoice_no, i.past_msme_window ? h("span", { class: "chip muted", style: { "margin-left": "8px" }, title: d.msme.caution, text: "MSME 45d" }) : null),
      dmy(i.invoice_date), i.due_date ? dmy(i.due_date) : "No due date", fmt(i.amount), fmt(i.paid), fmt(i.outstanding),
      h("span", { class: "chip " + ({ overdue: "broken", not_due: "pending", due_today: "pending", paid: "muted", no_due_date: "muted", overpaid: "muted" }[i.status] || "muted"),
        text: { overdue: `${i.days_overdue}d overdue`, not_due: "Not due", due_today: "Due today", paid: "Paid", no_due_date: "No due date", overpaid: "Overpaid" }[i.status] }),
      i.dispute ? h("span", { class: "chip conf medium", text: `Disputed: ${categoryName(i.dispute.category)}` })
        : (canWrite && Number(i.outstanding) > 0 ? h("button", { type: "button", class: "btn small ghost", onclick: () => openDispute(i.invoice_no, d.customer) }, "Dispute") : ""),
    ]);
    grid.append(card("c12", 2, h("h2", { text: "Invoices", style: { "text-align": "left", "margin-bottom": "10px" } }), simpleTable(["Invoice", "Invoice date", "Due", "Amount", "Paid", "Outstanding", "Status", ""], invRows, { right: [3, 4, 5] })));
    const promRows = d.promises.map((x) => [dmy(x.date), x.amount ? fmt(x.amount) : "–",
      h("span", { class: "chip " + ({ kept: "pending", broken: "broken", open: "pending", cancelled: "muted" }[x.effective]), text: { kept: "Kept", broken: "Broken", open: "Pending", cancelled: "Cancelled" }[x.effective] }), x.note || ""]);
    grid.append(card("c6 r-c12", 3, h("h2", { text: "Promise history", style: { "text-align": "left", "margin-bottom": "10px" } }),
      simpleTable(["Promised for", "Amount", "Outcome", "Note"], promRows, { right: [1] }) || h("div", { class: "empty", text: "No promises recorded yet." })));
    grid.append(card("c6 r-c12", 4, h("h2", { text: "Contact history", style: { "text-align": "left", "margin-bottom": "10px" } }),
      d.followups.length ? h("ul", { class: "timeline" }, d.followups.map((f) => h("li", {}, h("span", { class: "tl-dot" }),
        h("div", {}, h("div", { text: f.outcome }), h("div", { class: "sub", text: `${f.channel} · ${f.actor} · ${when(f.ts)}${f.note ? " · " + f.note : ""}` }))))) : h("div", { class: "empty", text: "Nobody has contacted this customer yet." })));
    if (d.disputes.length) {
      grid.append(card("c12", 5, h("h2", { text: "Disputes", style: { "text-align": "left", "margin-bottom": "10px" } }),
        simpleTable(["Invoice", "About", "Owner", "Status", "Note", ""], d.disputes.map((x) => [x.invoice_no, categoryName(x.category), x.owner || "–",
          h("span", { class: "chip " + (x.status === "open" ? "conf medium" : "muted"), text: x.status === "open" ? "Open" : "Resolved" }), x.status === "open" ? x.note : x.resolution_note,
          x.status === "open" && canWrite ? h("button", { type: "button", class: "btn small ghost", text: "Resolve", onclick: () => openResolveDispute(x) }) : ""]))));
    }
    root.append(grid);
    return root;
  });

  /* ---------- disputes ---------- */
  CO.registerView("disputes", "Disputes", "disputes", (d) => {
    const open = d.disputes.open, done = d.disputes.resolved;
    const row = (x, withAction) => [custLink(x.customer), x.invoice_no, categoryName(x.category), x.owner || "–", fmt(x.outstanding), dmy(x.opened_ts.slice(0, 10)), x.note || "",
      withAction && writable() ? h("button", { type: "button", class: "btn small ghost", text: "Resolve", onclick: () => openResolveDispute(x) }) : (withAction ? "" : x.resolution_note || "")];
    const head = viewHead("Disputes", "Invoices a customer has questioned. They stay in the totals but are kept out of the daily chase list until resolved.",
      writable() ? h("button", { type: "button", class: "btn small", onclick: () => openDispute() }, icon("plus"), "New dispute") : null);
    const openCard = card("c12", 0, h("h2", { text: `Open (${open.length})`, style: { "text-align": "left", "margin-bottom": "10px" } }),
      simpleTable(["Customer", "Invoice", "About", "Owner", "Outstanding", "Opened", "Details", ""], open.map((x) => row(x, true)), { right: [4] }) || h("div", { class: "empty", text: "No open disputes. When a customer questions an invoice, mark it here so nobody keeps chasing it." }));
    const doneCard = done.length ? card("c12", 1, h("h2", { text: "Recently resolved", style: { "text-align": "left", "margin-bottom": "10px" } }),
      simpleTable(["Customer", "Invoice", "About", "Owner", "Outstanding", "Opened", "Resolution"], done.map((x) => row(x, false)), { right: [4] })) : null;
    return h("div", {}, head, h("div", { class: "grid" }, openCard, doneCard));
  });

  /* ---------- credit watch ---------- */
  CO.registerView("credit", "Credit", "credit", (d) => {
    const ladder = h("div", { class: "ladder" }, [1, 2, 3, 4].map((n) => h("div", { class: `rung s${n}` }, h("strong", { text: `${n} · ${STAGE_NAMES[n]}` }),
      h("span", { text: { 1: "Recently overdue: a friendly reminder is enough.", 2: "Over 30 days, or a promise was broken: follow up firmly.",
        3: "Over 60 days, two broken promises, a large amount or over the credit limit: the owner should call.", 4: "Over 90 days, three broken promises, or over the limit and old: consider pausing new credit." }[n] }))));
    const rows = d.credit_watch.map((c) => {
      const util = c.credit_limit ? Math.min(1, Number(c.total_outstanding) / Number(c.credit_limit)) : null;
      return [custLink(c.customer), stageChip(c.stage, c.stage_label, c.stage_why), fmt(c.overdue), fmt(c.total_outstanding), c.credit_limit ? fmt(c.credit_limit) : h("span", { class: "sub", text: "none set" }),
        util === null ? "" : h("div", { class: "utilbar" + (c.over_limit ? " over" : "") }, h("i", { style: { "--w": util * 100 + "%" } })), c.stage_why];
    });
    return h("div", {}, viewHead("Credit watch", "Customers who may need a firmer conversation or a look at their credit terms. These are suggestions from visible rules; the decision is yours."),
      h("div", { class: "grid" }, card("c12", 0, ladder),
        card("c12", 1, h("h2", { text: "Customers to look at", style: { "text-align": "left", "margin-bottom": "10px" } }),
          simpleTable(["Customer", "Stage", "Overdue", "Total owed", "Credit limit", "Used", "Why"], rows, { right: [2, 3] }) || h("div", { class: "empty", text: "Nobody needs a firmer conversation right now." }),
          h("p", { class: "sub", style: { "margin-top": "12px" }, text: "Set a credit limit from a customer's page (Edit details) and it will show here and in the work queue." }))));
  });

  /* ---------- reminders ---------- */
  CO.registerView("reminders", "Reminders", "reminders", (d) => {
    const names = d.customers.filter((c) => Number(c.overdue) > 0);
    if (!state.remindFor || !names.some((c) => c.customer === state.remindFor)) state.remindFor = names.length ? names[0].customer : null;
    const out = h("div"), list = h("ul", { class: "picklist" });
    let lang = store.get("lang", "en");
    const sender = store.get("sender", ""), company = store.get("company", "");
    const langSeg = h("div", { class: "seg" });
    const stages = Object.fromEntries(d.queue.map((q) => [q.customer, q]));
    async function show() {
      if (!state.remindFor) { out.replaceChildren(h("div", { class: "empty", text: "Nobody has overdue invoices." })); return; }
      out.replaceChildren(spinner("Preparing draft…"));
      langSeg.replaceChildren(...[["en", "English"], ["hinglish", "Hinglish"]].map(([id, t]) => h("button", { type: "button", "aria-pressed": String(lang === id), text: t, onclick: () => { lang = id; store.set("lang", id); show(); } })));
      try {
        const j = await getJSON("/api/draft", { customer: state.remindFor, sender, company, language: lang });
        const block = (title, text) => h("div", { style: { "margin-bottom": "18px" } }, h("div", { class: "card-head" }, h("h2", { text: title, style: { "font-size": "16px" } }),
          h("button", { type: "button", class: "btn ghost small", onclick: () => copy(text) }, icon("copy"), "Copy")), h("div", { class: "draft", text }));
        out.replaceChildren(h("div", { class: "send-head", style: { "margin-bottom": "14px" } }, h("div", {}, h("span", { class: "chip " + (j.template === "promise_followup" ? "broken" : j.template === "firm" ? "call" : "remind"), text: j.template.replace("_", " ") }),
          h("span", { class: "sub", text: "  Draft only. Nothing is sent from here." })), h("div", { class: "head-actions" }, langSeg,
          h("button", { type: "button", class: "btn small", onclick: () => openSend(state.remindFor, lang) }, icon("chat"), "Send…"),
          writable() ? h("button", { type: "button", class: "btn small ghost", onclick: () => CO.openFollowup(state.remindFor) }, icon("phone"), "Log follow-up") : null)),
          block("Email: " + j.subject, j.email), block("WhatsApp", j.whatsapp));
      } catch (e) { out.replaceChildren(h("div", { class: "banner", text: "Could not build the draft: " + e.message })); }
    }
    names.forEach((c) => { const q = stages[c.customer]; list.append(h("li", {}, h("button", { type: "button", "aria-pressed": String(c.customer === state.remindFor),
      onclick: (e) => { state.remindFor = c.customer; CO.$$("button", list).forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); show(); } },
      h("span", {}, c.customer, q ? h("span", { class: `chip stagechip s${q.stage}`, style: { "margin-left": "8px" }, text: String(q.stage) }) : null), h("span", { class: "num sub", text: compact(Number(c.overdue)) })))); });
    const hint = !sender || !company ? note("Tip: set your name and company in Settings so drafts are signed correctly.") : null;
    show();
    return h("div", {}, viewHead("Reminder drafts", "Fact-only messages built from the invoice data, in English or Hinglish. You review and send; nothing goes out automatically."), hint,
      h("div", { class: "two" }, card("", 0, list), card("", 1, out)));
  });

  /* ---------- drop zone ---------- */
  let dropCount = 0;
  function dropzone(onFile, label, sub) {
    const id = "drop-" + ++dropCount;
    const input = h("input", { type: "file", accept: ".csv,.xlsx,.xlsm", class: "sr-only", id });
    const zone = h("label", { class: "drop", for: id, tabindex: "0" }, icon("import"), h("strong", { text: label }), h("span", { class: "sub", text: sub || "Drop a file here, or click to choose. .csv or .xlsx, up to 10 MB." }), input);
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("over"));
    zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) onFile(f); });
    input.addEventListener("change", () => { const f = input.files[0]; input.value = ""; if (f) onFile(f); });
    zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    return zone;
  }
  const statTile = (label, value, hint, cls = "") => h("div", { class: "kpi mini glass " + cls }, h("div", { class: "label", text: label }), h("div", { class: "value plain num", text: value }), h("div", { class: "hint", text: hint || "" }));

  /* ---------- import wizard: preview, confirm, then confirm what was paid ---------- */
  CO.registerView("import", "Import", "import", () => {
    const root = h("div"), body = h("div");
    root.append(viewHead("Import", "Bring in the latest outstanding-invoice export. You see exactly what would change before anything is saved."), body);
    if (!writable()) { body.append(card("c12", 0, readOnlyNote())); return root; }
    pickStep();
    return root;

    function steps(n) {
      return h("ol", { class: "steps", "aria-label": "Progress" }, ["Choose file", "Check the changes", "Confirm what was paid"].map((t, i) => h("li", { class: i + 1 === n ? "now" : (i + 1 < n ? "done" : ""), "aria-current": i + 1 === n ? "step" : null }, h("b", { text: String(i + 1) }), t)));
    }
    function pickStep(error) {
      let credit = false, days = "30";
      const cb = h("input", { type: "checkbox", id: "imp-credit" }), n = h("input", { id: "imp-days", value: days, inputmode: "numeric", class: "tiny", "aria-label": "Credit days" });
      const busy = h("div");
      const onFile = async (file) => {
        busy.replaceChildren(spinner(`Reading ${file.name}…`));
        try {
          const p = await upload("/api/import/preview", file, cb.checked ? { credit_days: n.value } : {});
          previewStep(p, cb.checked ? n.value : "");
        } catch (e) { pickStep(e.message); }
      };
      body.replaceChildren(steps(1), card("c12", 0,
        error ? h("div", { class: "form-error", style: { "margin-bottom": "14px" }, role: "alert", text: error }) : null,
        dropzone(onFile, "Drop your invoice export here"),
        h("div", { class: "field", style: { "margin-top": "16px" } }, h("label", { class: "checkrow", for: "imp-credit" }, cb, h("span", {}, "Some invoices have no due date. Fill them in as invoice date + ", n, " days (recorded in the log)."))),
        h("p", { class: "sub", text: "Works with an Excel or CSV export from Tally, Zoho, Busy or a spreadsheet: it looks for the columns invoice number, customer, invoice date, due date and amount or outstanding. Files stay on this computer and are deleted after use." }), busy));
    }
    function previewStep(p, creditDays) {
      const df = p.diff, bad = p.error_count > 0;
      const optSnap = h("input", { type: "checkbox", id: "imp-snap", checked: true });
      const optErr = h("input", { type: "checkbox", id: "imp-err" });
      const apply = h("button", { type: "button", class: "btn", text: "Save this import", onclick: async (e) => {
        const btn = e.currentTarget; btn.disabled = true; btn.textContent = "Saving…";
        try {
          const needConfirm = df.missing_count > 0;
          const out = await post("/api/import/apply", { upload_id: p.upload_id, allow_errors: optErr.checked, take_snapshot: optSnap.checked && !needConfirm, credit_days: creditDays === "" ? null : Number(creditDays), actor: actor() });
          await load(true);
          if (needConfirm) confirmStep(out, optSnap.checked); else doneStep(out, null);
        } catch (ex) { btn.disabled = false; btn.textContent = "Save this import"; CO.$("#imp-error").textContent = ex.message; CO.$("#imp-error").hidden = false; }
      } });
      const problems = bad ? card("c12", 1, h("h2", { text: `Rows with problems (${p.error_count})`, style: { "text-align": "left", "margin-bottom": "6px" } }),
        h("p", { class: "sub", text: "These rows are left out. Fix them in your file and upload again, or tick the box below to save the good rows now." }),
        simpleTable(["Row", "Problem"], p.errors.map((x) => [String(x.row), x.message])), p.error_count > p.errors.length ? h("p", { class: "sub", text: `…and ${p.error_count - p.errors.length} more.` }) : null) : null;
      const changed = df.changed_count ? card("c12", 2, h("h2", { text: `Changed invoices (${df.changed_count})`, style: { "text-align": "left", "margin-bottom": "10px" } }),
        simpleTable(["Invoice", "Customer", "Paid before", "Paid now", "Change"], df.changed.map((c) => [c.invoice_no, c.customer, fmt(c.paid_before), fmt(c.paid_after),
          h("span", { class: "chip " + (Number(c.delta) > 0 ? "pending" : Number(c.delta) < 0 ? "broken" : "muted"), text: (Number(c.delta) > 0 ? "+" : "") + fmt(c.delta) })]), { right: [2, 3] }),
        df.changed_count > df.changed.length ? h("p", { class: "sub", text: `Showing the first ${df.changed.length}.` }) : null) : null;
      const reversals = df.reversals.length ? card("c12", 3, h("div", { class: "note warn", text: `${plural(df.reversals.length, "invoice")} now show LESS paid than before (${df.reversals.slice(0, 3).map((r) => r.invoice_no).join(", ")}${df.reversals.length > 3 ? "…" : ""}). That can be a reversed receipt or a mistake in the export. Check before saving.` })) : null;
      const missing = df.missing_count ? card("c12", 4, h("h2", { text: `Not in this file (${df.missing_count})`, style: { "text-align": "left", "margin-bottom": "6px" } }),
        h("p", { class: "sub", text: `You already have ${plural(df.missing_count, "unpaid invoice")} (${fmt(df.missing_total)}) that are not in this file. Accounting exports often drop an invoice once it is paid, but that is not certain, so nothing is closed automatically. After saving you can confirm which ones were paid.` })) : null;
      body.replaceChildren(steps(2), h("div", { class: "grid" },
        card("c12", 0, h("div", { class: "card-head" }, h("h2", { text: `${p.filename}${p.sheet ? " · sheet " + p.sheet : ""}` }), h("button", { type: "button", class: "btn small ghost", text: "Choose another file", onclick: () => pickStep() })),
          h("div", { class: "statgrid" }, statTile("Invoices read", String(p.valid), p.duplicates ? `${p.duplicates} exact duplicate rows ignored` : ""), statTile("New", String(df.new_count), df.new_count ? "not seen before" : ""),
            statTile("Changed", String(df.changed_count), `${df.unchanged} unchanged`), statTile("Payments seen", fmt0(Number(df.payments_seen)), "increases in amounts paid"),
            statTile("May be paid", String(df.missing_count), df.missing_count ? fmt0(Number(df.missing_total)) + " not in file" : "")),
          p.warnings.length ? h("ul", { class: "briefing", style: { "margin-top": "14px" } }, p.warnings.map((w) => h("li", { class: "brief warn" }, icon("alert"), h("span", { text: w })))) : null),
        problems, reversals, changed, missing,
        card("c12", 5, h("div", { id: "imp-error", class: "form-error", role: "alert", hidden: true }),
          h("label", { class: "checkrow", for: "imp-snap" }, optSnap, h("span", { text: "Record a snapshot after saving, so this week is measured against the baseline" })),
          bad ? h("label", { class: "checkrow", for: "imp-err" }, optErr, h("span", { text: `Save the ${p.valid} good rows even though ${plural(p.error_count, "row")} had problems` })) : null,
          h("div", { class: "modal-actions", style: { "justify-content": "flex-start" } }, apply))));
    }
    function confirmStep(out, wantSnapshot) {
      const checks = out.missing.map((m) => ({ ...m, box: h("input", { type: "checkbox", "aria-label": `${m.invoice_no} was paid in full` }) }));
      const err = h("div", { class: "form-error", role: "alert", hidden: true });
      const finish = async (markSelected, btn) => {
        btn.disabled = true; err.hidden = true;
        try {
          const chosen = markSelected ? checks.filter((c) => c.box.checked) : [];
          let closed = null;
          if (chosen.length) closed = await post("/api/invoice/settle", { items: chosen.map((c) => ({ invoice_no: c.invoice_no, customer: c.customer })), note: "confirmed paid during import", actor: actor() });
          let snap = null;
          if (wantSnapshot) snap = await post("/api/snapshot", { as_of: todayISO(), actor: actor() });
          await load(true); doneStep(out, { closed, snap });
        } catch (ex) { err.textContent = ex.message; err.hidden = false; btn.disabled = false; }
      };
      const all = h("button", { type: "button", class: "btn small ghost", text: "Tick all", onclick: () => { const on = checks.some((c) => !c.box.checked); checks.forEach((c) => { c.box.checked = on; }); } });
      const go1 = h("button", { type: "button", class: "btn", text: "Mark ticked as paid in full", onclick: (e) => finish(true, e.currentTarget) });
      const skip = h("button", { type: "button", class: "btn ghost", text: "Keep them all open", onclick: (e) => finish(false, e.currentTarget) });
      body.replaceChildren(steps(3), h("div", { class: "grid" }, card("c12", 0, h("div", { class: "card-head" }, h("h2", { text: "Which of these were paid?" }), all),
        h("p", { class: "sub", text: `The import is saved. These ${out.missing_count} unpaid invoices were not in the file. Tick the ones you know were paid in full; anything you leave unticked stays open and keeps being followed up.` }),
        simpleTable(["Paid?", "Invoice", "Customer", "Still owed"], checks.map((c) => [c.box, c.invoice_no, c.customer, fmt(c.outstanding)]), { right: [3] }),
        out.missing_count > out.missing.length ? h("p", { class: "sub", text: `Showing the first ${out.missing.length} of ${out.missing_count}.` }) : null, err,
        h("div", { class: "modal-actions", style: { "justify-content": "flex-start" } }, go1, skip))));
    }
    function doneStep(out, extra) {
      const lines = [`${out.inserted} new, ${out.updated} changed, ${out.unchanged} unchanged.`, `Payments seen in this import: ${fmt(out.payments_seen)}.`];
      if (extra && extra.closed) lines.push(`${plural(extra.closed.settled, "invoice")} confirmed paid in full (${fmt(extra.closed.total)} closed).`);
      const snap = (extra && extra.snap) || out.snapshot;
      if (snap) lines.push(`Snapshot recorded for ${dmy(snap.as_of)}.`);
      out.warnings.forEach((w) => lines.push("Note: " + w));
      body.replaceChildren(steps(4), h("div", { class: "grid" }, card("c12", 0, h("h2", { text: "All saved" }), h("ul", { class: "briefing" }, lines.map((l) => h("li", { class: "brief ok" }, icon("check"), h("span", { text: l })))),
        h("div", { class: "modal-actions", style: { "justify-content": "flex-start" } }, h("a", { class: "btn", href: "#/queue" }, "See today's work queue"), h("a", { class: "btn ghost", href: "#/dashboard" }, "Dashboard"), h("button", { type: "button", class: "btn ghost", text: "Import another file", onclick: () => pickStep() })))));
    }
  });

  /* ---------- bank statement matching: suggestions only ---------- */
  CO.registerView("payments", "Payments", "payments", () => {
    const root = h("div"), body = h("div");
    root.append(viewHead("Match bank credits", "Upload a bank statement and see which customer and invoices each credit may be for. These are suggestions with reasons; nothing is changed and balances still come from your accounting export."), body);
    if (!writable()) { body.append(card("c12", 0, readOnlyNote())); return root; }
    pick();
    return root;

    function pick(error) {
      const busy = h("div");
      body.replaceChildren(card("c12", 0, error ? h("div", { class: "form-error", style: { "margin-bottom": "14px" }, role: "alert", text: error }) : null,
        dropzone(async (file) => {
          busy.replaceChildren(spinner(`Reading ${file.name}…`));
          try { show(await upload("/api/statement/match", file, state.asOf ? { as_of: state.asOf } : {}), file.name); } catch (e) { pick(e.message); }
        }, "Drop your bank statement here", "A CSV or Excel statement with a narration column and a credit/deposit column."),
        h("p", { class: "sub", style: { "margin-top": "12px" }, text: "The statement is read on this computer, matched, and deleted. Matching looks for invoice numbers and customer names in the narration and for amounts that equal what a customer owes, and flags amounts a few percent short that could be TDS." }), busy));
    }
    function show(out, name) {
      let filter = "all";
      const list = h("div"), segs = h("div", { class: "seg", role: "group", "aria-label": "Filter by match" });
      const counts = { high: 0, medium: 0, low: 0, none: 0 }; out.suggestions.forEach((s) => counts[s.confidence]++);
      const paint = () => {
        segs.replaceChildren(...[["all", `All ${out.suggestions.length}`], ["high", `High ${counts.high}`], ["medium", `Possible ${counts.medium}`], ["low", `Weak ${counts.low}`], ["none", `None ${counts.none}`]].map(([id, t]) =>
          h("button", { type: "button", "aria-pressed": String(filter === id), text: t, onclick: () => { filter = id; paint(); } })));
        const rows = out.suggestions.filter((s) => filter === "all" || s.confidence === filter).map((s) => {
          const prom = s.promise_ids.map((id) => state.data.promises.find((p) => p.id === id)).filter(Boolean)[0];
          return [s.date ? dm(s.date) : "–", h("div", {}, h("div", { class: "trunc", title: s.narration, text: s.narration || "(no narration)" }), s.reference ? h("div", { class: "sub", text: "Ref " + s.reference }) : null), fmt(s.amount),
            h("div", {}, confChip(s.confidence), s.customer ? h("div", {}, custLink(s.customer), s.invoice_nos.length ? h("span", { class: "sub", text: "  " + s.invoice_nos.join(" + ") }) : null) : null,
              h("ul", { class: "reasons show" }, s.reasons.map((r) => h("li", { text: r })))),
            h("div", { class: "cellactions" }, s.customer && writable() ? h("button", { type: "button", class: "btn small ghost", text: "Note it",
              onclick: () => CO.openFollowup(s.customer, { channel: "other", outcome: `Payment of ${fmt(s.amount)} received${s.reference ? " (ref " + s.reference + ")" : ""}`, note: s.narration }) }) : null,
              prom && writable() ? h("button", { type: "button", class: "btn small good", text: "Promise kept", onclick: () => CO.openResolve(prom, "kept") }) : null)];
        });
        list.replaceChildren(simpleTable(["Date", "Narration", "Credit", "Suggested match", ""], rows, { right: [2], cls: "matches" }) || h("div", { class: "empty", text: "Nothing in this group." }));
      };
      paint();
      body.replaceChildren(h("div", { class: "grid" }, card("c12", 0, h("div", { class: "card-head" }, h("h2", { text: `${name}: ${plural(out.credits, "credit")} found` }), h("button", { type: "button", class: "btn small ghost", text: "Choose another file", onclick: () => pick() })),
        note("Suggestions only. This does not change any balance or mark anything paid. When your accounting export shows the payment, import it and the balances update."),
        out.skipped ? h("p", { class: "sub", text: `${out.skipped} debit or empty lines were ignored.` }) : null,
        out.errors.length ? h("div", { class: "note warn", text: `${plural(out.errors.length, "line")} could not be read: ${out.errors.slice(0, 3).join("; ")}` }) : null,
        out.truncated ? h("div", { class: "note warn", text: "Only the first 500 credits are shown." }) : null, segs), card("c12", 1, list)));
    }
  });

  /* ---------- call sheet: a printable page for people away from the screen ---------- */
  CO.registerView("callsheet", "Call sheet", null, (d) => {
    const company = store.get("company", "");
    const rows = d.queue.map((q, i) => [String(i + 1), h("div", {}, h("strong", { text: q.customer }), q.contact.person ? h("div", { class: "sub", text: q.contact.person }) : null),
      q.contact.phone || "—", fmt0(Number(q.overdue)), `${q.oldest_days}d`, `${q.stage} · ${q.stage_label}`,
      q.days_since_contact === null ? "never" : `${q.days_since_contact}d ago`, h("span", { class: "fine", text: q.reasons.join("; ") }), "", ""]);
    const head = viewHead(`Call sheet · ${dmy(d.as_of)}`, `${plural(d.queue.length, "customer")} to contact${company ? " · " + company : ""}`,
      h("div", { class: "head-actions no-print" }, back("#/queue", "Work queue"), h("button", { type: "button", class: "btn small", onclick: () => window.print() }, icon("print"), "Print")));
    const table = simpleTable(["#", "Customer", "Phone", "Overdue", "Oldest", "Stage", "Last contact", "Why", "What they said", "Next date"], rows, { right: [3, 4], cls: "callsheet" })
      || h("div", { class: "empty", text: "Nobody to chase today." });
    return h("div", { class: "printable" }, head, h("div", { class: "grid" }, card("c12", 0, table,
      h("p", { class: "sub print-foot", text: "Prepared from the data supplied on this date. Scores come from visible rules; the order is a suggestion, not an instruction." }))));
  });

  /* ---------- activity: everything that changed, and who did it ---------- */
  function describe(e) {
    const a = e.after || {}, b = e.before || {};
    switch (e.action) {
      case "invoice.created": return ["Invoice added", `${rupees(a.amount_paise)}${a.due_date ? ", due " + dmy(a.due_date) : ""}`];
      case "invoice.updated": {
        const bits = [];
        if (a.paid_paise !== b.paid_paise) bits.push(`paid ${rupees(b.paid_paise)} → ${rupees(a.paid_paise)}`);
        if (a.amount_paise !== b.amount_paise) bits.push(`amount ${rupees(b.amount_paise)} → ${rupees(a.amount_paise)}`);
        if (a.due_date !== b.due_date) bits.push(`due ${b.due_date ? dmy(b.due_date) : "none"} → ${a.due_date ? dmy(a.due_date) : "none"}`);
        return ["Invoice changed by an import", bits.join("; ") || "details updated"];
      }
      case "invoice.settled": return ["Marked paid in full", `${rupees(a.amount_paise - b.paid_paise)} closed`];
      case "followup.logged": return ["Contact logged", `${a.channel}: ${a.outcome}`];
      case "promise.created": return ["Promise recorded", `to pay by ${dmy(a.promised_date)}`];
      case "promise.kept": return ["Promise marked kept", ""];
      case "promise.broken": return ["Promise marked broken", ""];
      case "promise.cancelled": return ["Promise cancelled", ""];
      case "dispute.opened": return ["Dispute opened", categoryName(a.category)];
      case "dispute.resolved": return ["Dispute resolved", ""];
      case "customer.updated": return ["Customer details changed", Object.keys(a).filter((k) => k !== "customer" && a[k] !== b[k]).map((k) => k.replace(/_/g, " ")).join(", ")];
      case "setting.changed": return ["Setting changed", Object.keys(a).map((k) => `${k.replace(/_/g, " ")}: ${b[k]} → ${a[k]}`).join("; ")];
      default: return [e.action, ""];
    }
  }
  CO.registerView("activity", "Activity", "activity", async () => {
    const root = h("div"), list = h("div"), more = h("div");
    let last = null;
    async function loadMore() {
      const j = await getJSON("/api/activity", last ? { limit: 100, before: last } : { limit: 100 });
      const rows = j.entries.map((e) => { const [t, detail] = describe(e); return [h("span", { class: "sub", text: when(e.ts) }), e.actor, t, e.label ? h("span", {}, e.label) : "", [detail, e.reason].filter(Boolean).join(" · ")]; });
      if (rows.length) list.append(simpleTable(["When", "Who", "What", "About", "Details"], rows, { cls: "activity" }));
      if (j.entries.length) last = j.entries[j.entries.length - 1].id;
      more.replaceChildren(j.entries.length === 100 ? h("button", { type: "button", class: "btn ghost", text: "Show older", onclick: loadMore }) : (last === null ? h("div", { class: "empty", text: "Nothing recorded yet." }) : null));
    }
    root.append(viewHead("Activity", "Every change to your records, who made it and when. Nothing here can be edited or deleted.", h("a", { class: "btn small ghost", href: "/backup.db", download: "" }, icon("download"), "Download backup")),
      h("div", { class: "grid" }, card("c12", 0, list, more)));
    try { await loadMore(); } catch (e) { list.append(h("div", { class: "banner", text: e.message })); }
    return root;
  });
})();
