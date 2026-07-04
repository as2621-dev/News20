/* blip — onboarding chat flow engine (scripted, deterministic).
   Port note for Claude Code: replace the canned copy + branching with the
   real agent; keep the interaction contract — bubbles, multi-select chips,
   composer live during subcategory steps, ONE follow-up per typed answer,
   20-story budget pinned, 30-channel grid, cluster checklist, final plan. */

/* ───────────────────────── data ───────────────────────── */

const CATS = [
  { id:"ai",       label:"AI",               accent:"#22D3EE",
    subs:["LLMs & frontier models","Chips & GPUs","AI policy","Robotics","AI startups","Open source","Agents & automation","AI safety","Coding assistants","Image & video models","Voice & audio AI","AI in enterprise"] },
  { id:"markets",  label:"Markets",          accent:"#22C55E",
    subs:["Stocks","Earnings","Macro & the Fed","Crypto","Bonds & rates","IPOs","Commodities","Currencies & FX","ETFs & funds","Real estate","Emerging markets","Options & volatility"] },
  { id:"tech",     label:"Tech & Science",   accent:"#22D3EE",
    subs:["Big Tech","Startups & VC","Space","Cybersecurity","Consumer gadgets","Science","Semiconductors","Cloud & infrastructure","Social platforms","Biotech","Quantum computing","Tech regulation"] },
  { id:"world",    label:"World & Politics", accent:"#EF4444",
    subs:["U.S. politics","Middle East","Europe","Asia-Pacific","Russia–Ukraine","Elections","China","India","Latin America","Africa","Trade & tariffs","Defense & security"] },
  { id:"sport",    label:"Sport",            accent:"#F59E0B",
    subs:["NFL","NBA","Soccer","Formula 1","Tennis","Cricket","MLB","NHL","Golf","UFC & boxing","College sports","Olympics"] },
  { id:"business", label:"Business",         accent:"#22C55E",
    subs:["Deals & M&A","CEOs & founders","Autos & EVs","Retail","Energy","Airlines & travel","Media & advertising","Banking & fintech","Supply chains","Labor & workplace","Antitrust","Luxury"] },
  { id:"culture",  label:"Culture",          accent:"#E8B7BC",
    subs:["Film & TV","Music","Gaming","Books","Internet culture","Streaming wars","Celebrity & fame","Art & design","Food","Fashion","Podcasts","Comedy"] },
  { id:"climate",  label:"Climate",          accent:"#E8B7BC",
    subs:["Clean energy","Climate policy","Extreme weather","Carbon markets","EVs & batteries","Nuclear","Solar & wind","Oceans","Wildfires & drought","Climate tech","Conservation","Green finance"] },
  { id:"health",   label:"Health",           accent:"#E8B7BC",
    subs:["Longevity","Mental health","Fitness","Medicine & pharma","Nutrition","Sleep","GLP-1 & obesity","Public health","Health tech","Cancer research","Brain science","Healthcare policy"] },
];

/* open-ended drill-down copy per subcategory; generic fallback below.
   Port note: the real agent should generate these from the subcategory. */
const DRILL = {
  "Formula 1": `<em>Formula 1</em> — would you like more race updates, or do you follow a team or a driver? Name it and I'll follow it.`,
  "Cricket":   `<em>Cricket</em> — what should I follow for you? A series, personalities, player updates, just the latest news — name it.`,
  "Tennis":    `<em>Tennis</em> — a player, the tour, the slams? Tell me and I'll track it.`,
  "NFL":       `<em>NFL</em> — a team, fantasy, the draft? Name it.`,
  "NBA":       `<em>NBA</em> — a team, a player, trade season? Name it.`,
  "Soccer":    `<em>Soccer</em> — which league, club, or competition? Name it and it's in.`,
  "Stocks":    `<em>Stocks</em> — any tickers, sectors, or themes you watch? Name them.`,
  "Earnings":  `<em>Earnings</em> — whose earnings matter most to you? Name the companies.`,
  "Crypto":    `<em>Crypto</em> — specific coins, or the whole space? Name what you hold or watch.`,
  "LLMs & frontier models": `<em>LLMs</em> — any labs or models you track closely? Name them.`,
  "Music":     `<em>Music</em> — artists, genres, the industry? Name what you'd follow.`,
  "Film & TV": `<em>Film & TV</em> — shows you're watching, directors, awards season? Name it.`,
};
const drillFor = (sub) => DRILL[sub] ||
  `Inside <em>${sub}</em> — anything specific I should watch? A name, a team, a storyline. Type it, or move on.`;

const YT = [
  ["The Compute Desk","ai"],["Frontier Notes","ai"],["Gradient Descent","ai"],["Lab to Launch","ai"],
  ["The Prompt Report","ai"],["Silicon Signal","ai"],["Macro Minute","markets"],["The Earnings Call","markets"],
  ["Chart & Verse","markets"],["Fed Watchers","markets"],["The Long Position","markets"],["Orbit & Beyond","tech"],
  ["The Teardown Lab","tech"],["Zero Day Brief","tech"],["Founder Radio","tech"],["Deep Field","tech"],
  ["The Situation Desk","world"],["Borderlines","world"],["The Ballot Box","world"],["Fourth Estate","world"],
  ["The Film Room","sport"],["Tactics Board","sport"],["Paddock Pass","sport"],["Full Court Notes","sport"],
  ["The Deal Memo","business"],["Founders' Ledger","business"],["The Cutting Room","culture"],["Replay Culture","culture"],
  ["Gridwatch","climate"],["The Checkup","health"],
];

const CLUSTERS = [
  { id:"labs",     name:"AI Lab Leaders",          cat:"ai",       handles:["@frontier_ceo","@lab_director","@scaling_lead"], n:14,
    more:["@model_release","@weights_open","@agi_timeline","@compute_gov","@eval_house","@red_team_lead","@alignment_memo","@tokens_per_sec","@benchmark_king","@sf_compute","@paperclip_max"],
    desc:"The people running the frontier labs — news straight from the source." },
  { id:"builders", name:"AI Builders & Researchers", cat:"ai",     handles:["@gpu_poor","@attn_is_all","@evals_guy"], n:12,
    more:["@cuda_kernel","@lora_finetune","@context_window","@mlops_daily","@tensor_dust","@inference_cost","@data_curator","@open_weights","@agent_loops"],
    desc:"Researchers and engineers posting what actually works." },
  { id:"macro",    name:"Macro Traders",           cat:"markets",  handles:["@rates_desk","@fedwhisper","@macro_tourist"], n:13,
    more:["@dot_plot","@yield_curve","@cpi_print","@qt_watch","@basis_trade","@recession_call","@soft_landing","@term_premium","@carry_trade","@dollar_bull"],
    desc:"Rates, the Fed, and where the money is moving." },
  { id:"earnings", name:"Earnings Whisperers",     cat:"markets",  handles:["@printseason","@guidance_cut","@tenk_diver"], n:10,
    more:["@beat_and_raise","@call_transcript","@margin_watch","@street_consensus","@afterhours_move","@buyback_bot","@guidance_watch"],
    desc:"Guidance, prints, and the after-hours reaction." },
  { id:"vc",       name:"VC & Startup Operators",  cat:"business", handles:["@seed_notes","@term_sheet","@founder_mode"], n:15,
    more:["@preseed_memo","@cap_table","@bridge_round","@dd_notes","@exit_multiple","@portfolio_ops","@lp_letters","@demo_day","@burn_rate","@pivot_watch","@saas_metrics","@moat_check"],
    desc:"Term sheets, fundraises, and operator lessons." },
  { id:"geo",      name:"Geopolitics Watchers",    cat:"world",    handles:["@map_room","@embassy_row","@osint_daily"], n:12,
    more:["@strait_watch","@sanctions_desk","@flight_tracker","@treaty_notes","@redline_memo","@summit_readout","@grain_shipments","@attache_notes","@ceasefire_map"],
    desc:"Analysts and OSINT accounts watching the map in real time." },
  { id:"critics",  name:"Sports Critics",          cat:"sport",    handles:["@press_row","@fourth_down","@hot_takes_fc"], n:11,
    more:["@beat_writer","@locker_room","@box_score","@film_session","@trade_deadline","@presser_quotes","@injury_report","@ref_watch"],
    desc:"The sharpest takes in the press box." },
  { id:"tacticians", name:"Football Tacticians",   cat:"sport",    handles:["@low_block","@xg_merchant","@half_space"], n:10,
    more:["@gegenpress","@inverted_fb","@set_piece_fc","@pass_map","@high_line","@counter_press","@false_nine"],
    desc:"Formations, xG, and why your team really lost." },
  { id:"paddock",  name:"F1 Paddock",              cat:"sport",    handles:["@pit_wall","@drs_zone","@parc_ferme"], n:10,
    more:["@tyre_deg","@quali_mode","@team_radio","@undercut_call","@wind_tunnel","@grid_penalty","@flexi_wing"],
    desc:"Inside the garages — strategy, upgrades, silly season." },
  { id:"film",     name:"Film & TV Critics",       cat:"culture",  handles:["@cold_open","@final_cut","@the_matinee"], n:12,
    more:["@opening_weekend","@directors_cut","@screener_szn","@long_take","@needle_drop","@prestige_tv","@festival_circuit","@rewatch_pod","@third_act"],
    desc:"What to watch, what to skip, and why." },
  { id:"climate",  name:"Climate Scientists",      cat:"climate",  handles:["@carbon_ledger","@grid_scale","@ppm_watch"], n:11,
    more:["@heat_dome","@sea_level_log","@methane_watch","@net_zero_audit","@permafrost_log","@el_nino_watch","@grid_storage","@cop_notes"],
    desc:"Scientists reading the data, not the headlines." },
  { id:"health",   name:"Health & Longevity Docs", cat:"health",   handles:["@span_md","@protocol_doc","@vo2maxx"], n:10,
    more:["@zone_two","@rct_or_bust","@biomarker_guy","@sleep_lab","@dose_response","@microbiome_md","@healthspan_hq"],
    desc:"Doctors and researchers on what actually works." },
];

const PHASES = ["INTERESTS","TUNE","YOUTUBE","X CLUSTERS","YOUR 30"];

/* ───────────────────────── state ───────────────────────── */

const S = {
  cats: [],            // selected cat objects
  subs: {},            // catId -> [labels]
  custom: {},          // catId -> [typed strings]
  tuning: [],          // [q, answer]
  alloc: {},           // catId -> count (sums to 20)
  yt: new Set(),       // channel names
  clusters: new Set(), // cluster ids
};

/* ───────────────────────── dom helpers ───────────────────────── */

const $ = (s, r = document) => r.querySelector(s);
const chat = $("#chat"), msgs = $("#msgs"), composer = $("#composer");
const cin = $("#cin"), csend = $("#csend"), chint = $("#chint");
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

function el(t, c, html) { const e = document.createElement(t); if (c) e.className = c; if (html != null) e.innerHTML = html; return e; }
function scrollDown() { requestAnimationFrame(() => chat.scrollTo({ top: chat.scrollHeight, behavior: reduced ? "auto" : "smooth" })); }
const wait = (ms) => new Promise(r => setTimeout(r, reduced ? Math.min(ms, 120) : ms));

function setPhase(i) {
  $("#phaseLabel").textContent = PHASES[i];
  const dots = $("#stepDots"); dots.innerHTML = "";
  PHASES.forEach((_, j) => {
    const d = el("span", "ob-step" + (j < i ? " done" : j === i ? " now" : ""));
    dots.appendChild(d);
  });
}

async function botSay(html, delay = 900) {
  const t = el("div", "row bot");
  t.appendChild(el("div", "typing", "<i></i><i></i><i></i>"));
  msgs.appendChild(t); scrollDown();
  await wait(delay);
  t.remove();
  const row = el("div", "row bot");
  row.appendChild(el("div", "who", '<span class="dot"></span>blip'));
  row.appendChild(el("div", "bub", html));
  msgs.appendChild(row); scrollDown();
}

function userSay(text) {
  const row = el("div", "row me");
  row.appendChild(el("div", "who", "you"));
  const b = el("div", "bub"); b.textContent = text;
  row.appendChild(b);
  msgs.appendChild(row); scrollDown();
}

/* multi-select chips → resolves with [labels]. opts: [{label, dot?}] */
function askChips(options, { confirmLabel = "Done →", min = 1, hint = "" } = {}) {
  return new Promise(resolve => {
    const wrap = el("div", "opts");
    const picked = new Set();
    const btnRow = el("div", "confirm");
    const btn = el("button"); btn.textContent = confirmLabel; btn.disabled = min > 0;
    const hintEl = el("span", "hint", hint);
    btnRow.appendChild(btn); if (hint) btnRow.appendChild(hintEl);

    options.forEach(o => {
      const c = el("button", "opt");
      c.innerHTML = (o.dot ? `<span class="cd" style="background:${o.dot};box-shadow:0 0 6px ${o.dot}"></span>` : "") + o.label;
      c.onclick = () => {
        c.classList.toggle("sel");
        c.classList.contains("sel") ? picked.add(o.label) : picked.delete(o.label);
        btn.disabled = picked.size < min;
        hintEl.textContent = picked.size ? `${picked.size} selected` : hint;
      };
      wrap.appendChild(c);
    });
    msgs.appendChild(wrap); msgs.appendChild(btnRow); scrollDown();

    btn.onclick = () => {
      wrap.remove(); btnRow.remove();
      const out = options.filter(o => picked.has(o.label)).map(o => o.label);
      resolve(out);
    };
  });
}

/* single-choice quick replies → resolves with label */
function askQuick(options) {
  return new Promise(resolve => {
    const wrap = el("div", "opts");
    options.forEach(label => {
      const c = el("button", "opt"); c.textContent = label;
      c.onclick = () => { wrap.remove(); resolve(label); };
      wrap.appendChild(c);
    });
    msgs.appendChild(wrap); scrollDown();
  });
}

/* open-ended step: composer live + one "nothing specific" escape chip.
   Resolves with typed text, or null if skipped. */
function askOpen(placeholder, skipLabel = "Nothing specific →") {
  return new Promise(resolve => {
    const wrap = el("div", "opts");
    const c = el("button", "opt"); c.textContent = skipLabel;
    c.onclick = () => { cleanup(); resolve(null); };
    wrap.appendChild(c);
    msgs.appendChild(wrap); scrollDown();
    composerLive(placeholder, v => { cleanup(); resolve(v); });
    function cleanup() { wrap.remove(); composerIdle(); }
  });
}

/* composer control: live(placeholder) returns a hook; each send calls onText */
let composerHook = null;
function composerLive(placeholder, onText) {
  composer.classList.remove("idle");
  cin.disabled = false; cin.placeholder = placeholder;
  composerHook = onText;
}
function composerIdle() {
  composer.classList.add("idle");
  cin.value = ""; cin.disabled = true; cin.placeholder = "Pick from the bubbles above";
  csend.classList.remove("live");
  composerHook = null;
}
cin.addEventListener("input", () => csend.classList.toggle("live", cin.value.trim().length > 0));
function fireSend() {
  const v = cin.value.trim();
  if (!v || !composerHook) return;
  cin.value = ""; csend.classList.remove("live");
  userSay(v); composerHook(v);
}
csend.addEventListener("click", fireSend);
cin.addEventListener("keydown", e => { if (e.key === "Enter") fireSend(); });

/* ───────────────────────── phase 1: interests ───────────────────────── */

async function phaseInterests() {
  setPhase(0); composerIdle();
  await botSay(`Hey — I'm <em>blip</em>. Every day I'll build you a briefing of exactly 30 stories, then we're done. No feed, no rabbit holes.`, 1100);
  await botSay(`First — tell me about the big categories you actually follow. Pick as many as you like.`, 1000);

  const picked = await askChips(
    CATS.map(c => ({ label: c.label, dot: c.accent })),
    { confirmLabel: "That's me →", hint: "pick at least one" }
  );
  S.cats = CATS.filter(c => picked.includes(c.label));
  userSay(picked.join(" · "));

  /* per-category subcategories */
  for (let i = 0; i < S.cats.length; i++) {
    const cat = S.cats[i];
    document.getElementById("device").style.setProperty("--accent", cat.accent);
    const opener = i === 0 ? `Nice picks. Let's go one by one — starting with` :
                   i === S.cats.length - 1 ? `Last one —` : `Next up —`;
    await botSay(`${opener} <em>${cat.label}</em>. What pulls you in here? Tap what fits, or type anything I'm missing.`);

    let typed = null;
    composerLive(`Type your own ${cat.label} interest…`, v => {
      typed = v;
      (S.custom[cat.id] = S.custom[cat.id] || []).push(v);
    });

    const subs = await askChips(
      cat.subs.map(s => ({ label: s })),
      { confirmLabel: `Done with ${cat.label} →`, min: 0, hint: "or type below" }
    );
    composerIdle();
    S.subs[cat.id] = subs;
    if (subs.length) userSay(subs.join(" · "));
    if (typed) await botSay(`Got it — <em>“${typed}”</em> is on the list too.`, 700);

    /* one open-ended drill-down per selected subcategory */
    for (const sub of subs) {
      await botSay(drillFor(sub));
      const ans = await askOpen(`${sub} — type what matters to you…`);
      if (ans) {
        (S.custom[cat.id] = S.custom[cat.id] || []).push(`${sub}: ${ans}`);
        await botSay(`Got it — I'll fold <em>“${ans}”</em> into your ${sub} coverage.`, 700);
      }
    }
  }
  document.getElementById("device").style.setProperty("--accent", "#22D3EE");
}

/* ───────────────────────── phase 2: tune + budget ───────────────────────── */

async function phaseTune() {
  setPhase(1);
  await botSay(`That's a clear picture. A few quick follow-ons to tune it —`, 900);

  const qs = [];
  if (S.cats.some(c => c.id === "markets"))
    qs.push({ q: `You said <em>Markets</em> — want earnings-day performance folded in as its own stories during the season?`, a: ["Yes, include earnings", "Just the big moves"] });
  if (S.cats.some(c => c.id === "ai" || c.id === "tech"))
    qs.push({ q: `On the tech side — more <em>product launches</em>, or the research and policy underneath them?`, a: ["Launches", "Research & policy", "Both"] });
  qs.push({ q: `When a story lands, do you want the quick signal or the why behind it?`, a: ["Just the signal", "Give me the why", "Depends on the story"] });
  qs.push({ q: `Should a couple of wildcard stories outside your picks sneak in each day?`, a: ["Yes, surprise me", "No, stay on target"] });

  for (const item of qs.slice(0, 4)) {
    await botSay(item.q);
    const a = await askQuick(item.a);
    userSay(a);
    S.tuning.push([item.q, a]);
  }

  /* 20-story budget */
  await botSay(`Here's how I'd split your <em>20 daily stories</em> across what you picked. Nudge anything — the total stays at 20.`, 1100);

  const n = S.cats.length, base = Math.floor(20 / n);
  S.cats.forEach((c, i) => S.alloc[c.id] = base + (i < 20 - base * n ? 1 : 0));

  await new Promise(resolve => {
    const card = el("div", "alloc");
    const ttl = el("div", "ttl", `STORY BUDGET <b id="allocN"></b>`);
    card.appendChild(ttl);
    S.cats.forEach(c => {
      const row = el("div", "arow");
      row.innerHTML = `<span class="nm"><span class="cd" style="background:${c.accent};box-shadow:0 0 6px ${c.accent}"></span>${c.label}</span>`;
      const st = el("div", "st");
      const minus = el("button", null, "–"), num = el("span", "n"), plus = el("button", null, "+");
      st.appendChild(minus); st.appendChild(num); st.appendChild(plus);
      row.appendChild(st); card.appendChild(row);
      minus.onclick = () => { if (S.alloc[c.id] > 0) { S.alloc[c.id]--; render(); } };
      plus.onclick = () => { if (total() < 20) { S.alloc[c.id]++; render(); } };
      c._num = num; c._minus = minus; c._plus = plus;
    });
    const bar = el("div", "abar");
    for (let i = 0; i < 20; i++) bar.appendChild(el("i"));
    card.appendChild(bar);
    const btnRow = el("div", "confirm");
    const btn = el("button"); btn.textContent = "Lock my 20 →";
    btnRow.appendChild(btn);
    msgs.appendChild(card); msgs.appendChild(btnRow); scrollDown();

    const total = () => Object.values(S.alloc).reduce((a, b) => a + b, 0);
    function render() {
      const t = total();
      const nEl = card.querySelector("#allocN");
      nEl.textContent = `${t} / 20`; nEl.classList.toggle("full", t === 20);
      S.cats.forEach(c => {
        c._num.textContent = S.alloc[c.id];
        c._minus.disabled = S.alloc[c.id] === 0;
        c._plus.disabled = t >= 20;
      });
      [...bar.children].forEach((seg, i) => seg.classList.toggle("on", i < t));
      btn.disabled = t !== 20;
    }
    render();
    btn.onclick = () => { btnRow.remove(); resolve(); };
  });

  userSay(S.cats.map(c => `${c.label} ${S.alloc[c.id]}`).join(" · "));
  await botSay(`Locked. That's your 20 — you can rebalance any time in settings.`, 800);
}

/* ───────────────────────── phase 3: youtube ───────────────────────── */

async function phaseYouTube() {
  setPhase(2);
  await botSay(`Now the video side. Based on everything you've told me, here are channels people with your profile follow — pick the ones you'd want <em>7 stories</em> a day from.`, 1200);

  const catRank = new Map(S.cats.map((c, i) => [c.id, i]));
  const sorted = [...YT].sort((a, b) =>
    (catRank.has(a[1]) ? catRank.get(a[1]) : 99) - (catRank.has(b[1]) ? catRank.get(b[1]) : 99));

  await new Promise(resolve => {
    const grid = el("div", "yt-grid");
    sorted.forEach(([name, catId]) => {
      const cat = CATS.find(c => c.id === catId);
      const initials = name.split(/\s+/).map(w => w[0]).slice(0, 2).join("");
      const tile = el("button", "yt");
      tile.innerHTML = `
        <span class="th" style="background:radial-gradient(120% 120% at 30% 20%, color-mix(in oklab, ${cat.accent} 38%, #0b1120) 0%, #070b14 78%)">${initials}
          <span class="tick"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"></path></svg></span>
        </span>
        <span class="nm">${name}</span>
        <span class="sub">${cat.label}</span>`;
      tile.onclick = () => {
        tile.classList.toggle("sel");
        tile.classList.contains("sel") ? S.yt.add(name) : S.yt.delete(name);
        btn.disabled = S.yt.size === 0;
        hint.textContent = S.yt.size ? `${S.yt.size} channels` : "pick a few";
      };
      grid.appendChild(tile);
    });
    const btnRow = el("div", "confirm");
    const btn = el("button"); btn.textContent = "Add these →"; btn.disabled = true;
    const hint = el("span", "hint", "pick a few");
    btnRow.appendChild(btn); btnRow.appendChild(hint);
    msgs.appendChild(grid); msgs.appendChild(btnRow); scrollDown();
    btn.onclick = () => { grid.querySelectorAll(".yt:not(.sel)").forEach(t => t.classList.add("ghosted")); btnRow.remove(); resolve(); };
  });

  userSay(`${S.yt.size} channels selected`);
  await botSay(`Good taste. I'll pull your <em>7</em> daily video stories from those.`, 800);
}

/* ───────────────────────── phase 4: x clusters ───────────────────────── */

async function phaseClusters() {
  setPhase(3);
  await botSay(`Last pick. On X, the good stuff lives in <em>clusters</em> — curated sets of 10–15 handles that actually know their beat. From your answers, these fit you:`, 1200);

  await new Promise(resolve => {
    /* group clusters under the user's primary categories, in pick order */
    const wrap = el("div", "cl-wrap");
    const selCatIds = S.cats.map(c => c.id);
    const restIds = [...new Set(CLUSTERS.map(c => c.cat))].filter(id => !selCatIds.includes(id));
    const groups = [];
    selCatIds.forEach(id => {
      const items = CLUSTERS.filter(cl => cl.cat === id);
      if (items.length) groups.push({ cat: CATS.find(c => c.id === id), items, extra: false });
    });
    const rest = restIds.flatMap(id => CLUSTERS.filter(cl => cl.cat === id));
    if (rest.length) groups.push({ cat: null, items: rest, extra: true });

    const btnRow = el("div", "confirm");
    const btn = el("button"); btn.textContent = "Follow these →"; btn.disabled = true;
    const hint = el("span", "hint", "pick what fits");
    btnRow.appendChild(btn); btnRow.appendChild(hint);

    groups.forEach(g => {
      const sec = el("div", "cl-sec");
      sec.innerHTML = g.extra
        ? `BEYOND YOUR PICKS`
        : `<span class="cd" style="background:${g.cat.accent};box-shadow:0 0 6px ${g.cat.accent}"></span>${g.cat.label} · ${g.items.length} CLUSTER${g.items.length > 1 ? "S" : ""}`;
      wrap.appendChild(sec);
      const grid = el("div", "cl-grid");
      g.items.forEach(cl => {
        const full = [...cl.handles, ...(cl.more || [])];
        const card = el("div", "cl");
        card.setAttribute("role", "button"); card.tabIndex = 0;
        card.innerHTML = `
          <span class="tick"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"></path></svg></span>
          <span class="cn">${cl.name}</span>
          <span class="hl">${cl.handles.map(h => `<span>${h}</span>`).join("")}</span>
          <button class="more">+${full.length - cl.handles.length} MORE ›</button>
          <span class="ds">${cl.desc}</span>`;
        const hl = card.querySelector(".hl"), mb = card.querySelector(".more");
        let open = false;
        mb.onclick = (e) => {
          e.stopPropagation();
          open = !open;
          hl.innerHTML = (open ? full : cl.handles).map(h => `<span>${h}</span>`).join("");
          mb.textContent = open ? "SHOW LESS ‹" : `+${full.length - cl.handles.length} MORE ›`;
        };
        card.onclick = () => {
          card.classList.toggle("sel");
          card.classList.contains("sel") ? S.clusters.add(cl.id) : S.clusters.delete(cl.id);
          btn.disabled = S.clusters.size === 0;
          hint.textContent = S.clusters.size ? `${S.clusters.size} clusters` : "pick what fits";
        };
        grid.appendChild(card);
      });
      wrap.appendChild(grid);
    });

    msgs.appendChild(wrap); msgs.appendChild(btnRow); scrollDown();
    btn.onclick = () => { wrap.querySelectorAll(".cl:not(.sel)").forEach(c => c.classList.add("ghosted")); btnRow.remove(); resolve(); };
  });

  userSay(`${S.clusters.size} clusters selected`);
}

/* ───────────────────────── phase 5: your 30 ───────────────────────── */

async function phaseSummary() {
  setPhase(4);
  await botSay(`That's everything I need. Here's your daily <em>30</em> — same shape every morning, and when it's done, it's done.`, 1200);

  const card = el("div", "plan");
  card.innerHTML = `<div class="ttl">YOUR DAILY 30 · DEFAULT MIX</div>`;
  const bar = el("div", "pbar");
  for (let i = 0; i < 30; i++) {
    const seg = el("i");
    seg.style.background = i < 20 ? "rgba(255,255,255,.75)" : i < 27 ? "rgba(255,255,255,.38)" : "#FACC15";
    bar.appendChild(seg);
  }
  card.appendChild(bar);
  const rows = [
    ["20", "STORIES", `from your ${S.cats.length} categories — ${S.cats.map(c => `${c.label} ${S.alloc[c.id]}`).join(", ")}`],
    ["7",  "YOUTUBE", `from ${S.yt.size} channels you picked`],
    ["3",  "X CLUSTERS", `from ${S.clusters.size} clusters you follow`],
  ];
  rows.forEach(([n, t, d]) => {
    const r = el("div", "prow");
    r.innerHTML = `<span class="num">${n}</span><span class="lb"><b>${t}</b><span>${d}</span></span>`;
    card.appendChild(r);
  });
  const cta = el("button", "cta"); cta.textContent = "Build my 30 →";
  card.appendChild(cta);
  msgs.appendChild(card); scrollDown();

  cta.onclick = async () => {
    cta.disabled = true; cta.style.opacity = ".5";
    await botSay(`Perfect — that's your briefing tuned. <span class="k">BUILDING YOUR 30 · READY IN ~40 SEC</span>`, 900);
  };
}

/* ───────────────────────── run ───────────────────────── */

(async function run() {
  composerIdle();
  await wait(500);
  await phaseInterests();
  await phaseTune();
  await phaseYouTube();
  await phaseClusters();
  await phaseSummary();
})();
