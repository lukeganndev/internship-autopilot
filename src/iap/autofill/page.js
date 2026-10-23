// Runs inside the application page (every frame). Exposes window.__iap with:
//   collect()            → descriptors of not-yet-handled form fields (and tags them data-iap-id)
//   banner(html, tone)   → a fixed status bar at the top of the page
//   mark(ids, needs)     → done / needs-you outlines
//   blockers()           → login, account creation or CAPTCHA currently on screen
//   applied()            → looks like a "thanks, we got your application" page
(() => {
  if (window.__iap) return;
  const txt = (e) => (e ? (e.innerText || e.textContent || "") : "").replace(/\s+/g, " ").trim();
  const shown = (e) => {
    if (!e) return false;
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    // opacity:0 catches the hidden "required" shadow inputs that dropdown widgets render under themselves
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  };
  const SKIP = new Set(["hidden", "submit", "button", "image", "reset", "password", "search"]);
  let next = 0;

  // Placeholder-ish text ("Select...", "Attach") says nothing about what the field is for.
  const vague = (s) => !s || s.length < 2 || /^(select|choose|please select|search|attach|upload|browse|type here)\b/i.test(s.trim());

  function labelOf(el) {
    const by = el.getAttribute("aria-labelledby");
    const candidates = [
      el.getAttribute("aria-label"),
      by && by.split(/\s+/).map((id) => txt(document.getElementById(id))).join(" "),
      el.labels && el.labels.length ? [...el.labels].map(txt).join(" ") : "",
      el.closest("label") ? txt(el.closest("label")) : "",
    ];
    for (let p = el.parentElement, i = 0; p && i < 4; p = p.parentElement, i++) {
      const l = p.querySelector("label, legend, [data-automation-id*='label' i], [class*='label' i]");
      if (l && l !== el && !l.contains(el)) candidates.push(txt(l));
    }
    candidates.push(el.placeholder, el.name && el.name.replace(/[_\[\]-]+/g, " "));
    return (candidates.find((c) => !vague(c)) || candidates.find(Boolean) || "").slice(0, 400);
  }

  function contextOf(el) {
    // For uploads: the input's id/name plus the text of the *largest* box that still holds only this
    // upload ("Resume/CV* Attach Dropbox ..."), since the nearest label is usually just "Attach".
    let best = "";
    for (let p = el.parentElement, i = 0; p && i < 7; p = p.parentElement, i++) {
      if (p.querySelectorAll("input[type=file]").length !== 1) break;
      best = txt(p);
    }
    return `${el.id || ""} ${el.name || ""} ${best}`.replace(/[_-]+/g, " ").trim().slice(0, 300);
  }

  function questionOf(el) {
    // For radios/checkboxes the label is the option ("Yes"); the question lives on the group.
    const fs = el.closest("fieldset");
    if (fs && fs.querySelector("legend")) return txt(fs.querySelector("legend"));
    const g = el.closest("[role=radiogroup], [role=group]");
    if (g) {
      const by = g.getAttribute("aria-labelledby");
      if (by) return txt(document.getElementById(by));
      if (g.getAttribute("aria-label")) return g.getAttribute("aria-label");
    }
    const own = labelOf(el);
    for (let p = el.parentElement, i = 0; p && i < 6; p = p.parentElement, i++) {
      const t = txt(p);
      if (t.length > own.length + 8) return t.slice(0, 400);
    }
    return "";
  }

  function kindOf(el) {
    const tag = el.tagName.toLowerCase(), role = el.getAttribute("role");
    if (tag === "select") return "select";
    if (tag === "textarea") return "textarea";
    if (tag === "button" || role === "button") return "listbox";  // Workday-style dropdown button
    if (role === "combobox" || el.getAttribute("aria-autocomplete")) return "combobox";
    const t = (el.getAttribute("type") || "text").toLowerCase();
    if (t === "file" || t === "radio" || t === "checkbox") return t;
    if (["email", "tel", "url", "number", "date"].includes(t)) return t === "date" ? "date" : "text";
    return "text";
  }

  function filled(el, kind) {
    if (kind === "select") return el.selectedIndex > 0 && !/^(select|choose|please)/i.test(el.options[el.selectedIndex]?.text || "");
    if (kind === "radio" || kind === "checkbox") return el.checked;
    if (kind === "listbox") return !/^(select|choose)/i.test(txt(el)) && txt(el) !== "";
    if (kind === "file") return el.files && el.files.length > 0;
    return (el.value || "").trim() !== "";
  }

  function collect() {
    const sel = "input, select, textarea, [role=combobox], button[aria-haspopup=listbox]";
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
      if (el.dataset.iapDone || el.closest("#iap-banner")) continue;
      const kind = kindOf(el), type = (el.getAttribute("type") || "").toLowerCase();
      if (el.tagName === "INPUT" && SKIP.has(type)) continue;
      if (el.disabled || el.readOnly) continue;
      const visibleish = shown(el) || kind === "file" || ((kind === "radio" || kind === "checkbox") && el.labels && [...el.labels].some(shown));
      if (!visibleish) continue;
      if (!el.dataset.iapId) el.dataset.iapId = String(++next) + "-" + Math.random().toString(36).slice(2, 6);
      out.push({
        id: el.dataset.iapId, kind, label: labelOf(el), group: el.name || "",
        question: kind === "radio" || kind === "checkbox" ? questionOf(el) : kind === "file" ? contextOf(el) : "",
        required: el.required || el.getAttribute("aria-required") === "true" || /\*\s*$/.test(labelOf(el)),
        filled: filled(el, kind),
        options: kind === "select" ? [...el.options].map((o) => o.text.trim()) : null,
      });
    }
    return out;
  }

  function banner(message, tone, items) {
    // Plain text only (labels come from the page itself), built with textContent.
    let b = document.getElementById("iap-banner");
    if (!b) {
      b = document.createElement("div");
      b.id = "iap-banner";
      b.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:10px 16px;font:14px/1.4 system-ui,sans-serif;" +
        "box-shadow:0 2px 8px rgba(0,0,0,.25);color:#111;max-height:40vh;overflow:auto";
      document.documentElement.appendChild(b);
    }
    b.style.display = "block";
    b.style.background = { ok: "#d1f5d3", warn: "#fff1b8", stop: "#ffd6d6" }[tone] || "#fff1b8";
    b.replaceChildren();
    const hide = document.createElement("a");
    hide.href = "#"; hide.textContent = "hide"; hide.style.cssText = "float:right;color:#333";
    hide.onclick = (e) => { e.preventDefault(); b.style.display = "none"; };
    const title = document.createElement("b");
    title.textContent = "Internship Autopilot · ";
    b.append(hide, title, document.createTextNode(message));
    if (items && items.length) {
      const ul = document.createElement("ul");
      ul.style.margin = "4px 0 0 18px";
      for (const it of items) { const li = document.createElement("li"); li.textContent = it; ul.append(li); }
      b.append(ul);
    }
  }

  function mark(ids, needs) {
    for (const id of ids) {
      const el = document.querySelector(`[data-iap-id="${id}"]`);
      if (!el) continue;
      if (!needs) el.dataset.iapDone = "1";  // "needs you" fields stay collectable so they clear once filled
      const box = el.type === "radio" || el.type === "checkbox" ? (el.closest("fieldset") || el.parentElement) : el;
      box.style.outline = needs ? "3px solid #f59e0b" : "2px solid #22c55e";
      box.style.outlineOffset = "2px";
      if (needs) box.title = "Internship Autopilot: needs you";
    }
  }

  function blockers() {
    const pw = [...document.querySelectorAll("input[type=password]")].filter(shown).length;
    const body = txt(document.body).toLowerCase();
    const captcha = [...document.querySelectorAll("iframe")].some((f) => shown(f) && f.getBoundingClientRect().height >= 150 &&
      /recaptcha\/api2\/bframe|hcaptcha|challenges\.cloudflare|turnstile|arkoselabs|funcaptcha|captcha/i.test(f.src || ""));
    if (captcha) return "captcha";
    if (pw >= 2 || (pw && /create (an |your )?account|sign up|register/.test(body))) return "account";
    if (pw === 1) return "login";
    return "";
  }

  function applied() {
    const t = txt(document.body).toLowerCase();
    return /thank you for (applying|your application|your interest)|application (has been |was )?(submitted|received)|we('ve| have) received your application|successfully (submitted|applied)/.test(t)
      && document.querySelectorAll("input:not([type=hidden]), textarea").length < 4;
  }

  window.__iap = { collect, banner, mark, blockers, applied };
})();
