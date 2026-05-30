/* News20 — app engine. Vanilla JS, no build. Drives the audio-first reel,
   karaoke captions, gestures, auto-advance, and every surface in §5. */
(function () {
  const { STORIES, SEGMENTS, FEED_TOTAL, FEED_START_INDEX, BIAS } = window.NEWS20_DATA;
  const app = document.getElementById("app");
  const device = document.getElementById("device");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ----- state -----
  const S = {
    idx: 0,
    playing: false,
    audioUnlocked: false,
    elapsed: 0,
    duration: 13000,
    raf: null,
    lastTs: 0,
    curSentence: -1,
    lateral: null, // 'detail' | 'voice'
    saved: new Set(),
    followed: new Set(["s1"]), // seed one so "what's new" has content
    coachShown: false,
    booted: false,
  };

  const feedPos = () => FEED_START_INDEX + S.idx; // 0-based
  const story = () => STORIES[S.idx];
  const seg = (st) => SEGMENTS[st.segment];

  // ----- tiny helpers -----
  const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const icon = (name, cls = "", size = 22) =>
    `<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const setAccent = (hex) => device.style.setProperty("--accent", hex);

  // blip wordmark — the i's tittle is a clear dot, with sound/radar waves
  // rippling out to the RIGHT of it. Built once, em-scaled, monochrome white.
  const blipSignal = (() => {
    const cy = 7, radii = [3.2, 6.2, 9.2], k = 0.695, s = 0.719; // cos/sin 46°, opens right
    const arc = (r, i) => {
      const x = (k * r).toFixed(2);
      const y1 = (cy - s * r).toFixed(2), y2 = (cy + s * r).toFixed(2);
      return `<path class="bw bw${i + 1}" d="M${x} ${y1} A${r} ${r} 0 0 1 ${x} ${y2}"/>`;
    };
    return `<svg class="blip-sig" viewBox="0 0 11 14" fill="none" aria-hidden="true">${radii.map(arc).join("")}</svg>`;
  })();
  const blipLogo = (px, cls = "") => `<span class="blip ${cls}" style="font-size:${px}px">bl<span class="bi">ı<i class="tittle"><b class="bdot"></b>${blipSignal}</i></span>p</span>`;

  // =====================================================================
  // REEL
  // =====================================================================
  let R = {}; // refs
  function mountReel() {
    const layer = h(`
      <div class="layer layer-reel">
        <div class="reel-bg" id="reel-bg"></div>
        <div class="ambient reel-ambient"></div>
        <div class="reel-scrim-top"></div>
        <div class="reel-scrim"></div>

        <!-- top chrome -->
        <div class="safe-top px-5 relative z-10" style="padding-top:62px">
          <div class="flex gap-[3px] mb-3" id="finite-bar"></div>
          <div class="flex items-center justify-between gap-3">
            <div class="flex items-baseline gap-2 min-w-0">
              ${blipLogo(20)}
              <span class="font-mono text-[9.5px] tracking-[0.08em] text-white/45 whitespace-nowrap">THU · MAY 29</span>
            </div>
            <div class="flex items-center gap-2.5 flex-none">
              <span class="font-mono text-[12px] font-semibold text-white/85 whitespace-nowrap" id="counter"></span>
              <button data-tap class="act-btn !w-9 !h-9 !rounded-pill" id="btn-profile" aria-label="Profile">${icon("profile", "", 18)}</button>
            </div>
          </div>
        </div>

        <!-- caption hero: centered in the middle (slightly below), centre-aligned -->
        <div class="flex-1 flex items-center justify-center px-6 relative z-10" id="caption-wrap">
          <div class="caption text-center w-full" id="caption" style="transform:translateY(34px)"></div>
        </div>

        <!-- headline zone (anchored low, left-aligned) -->
        <div class="px-5 relative z-10" id="headline-zone"></div>

        <!-- speaker + per-story progress -->
        <div class="px-5 pt-4 relative z-10">
          <div class="flex items-center mb-2">
            <span class="seg-chip text-white/80" id="speaker"></span>
          </div>
          <div class="story-progress"><i id="story-prog-fill"></i></div>
        </div>

        <!-- action row (low row — least conflict with lateral swipes) -->
        <div class="px-5 pt-4 pb-2 relative z-10 flex items-end justify-between" id="action-row"></div>

        <div class="safe-bottom"></div>

        <!-- coach peeks -->
        <div class="coach-peek-r" id="peek-r"><div class="font-mono text-[8px] text-white/60 rotate-90 whitespace-nowrap">READ ›</div></div>
        <div class="coach-peek-l" id="peek-l"><div class="font-mono text-[8px] text-white/60 rotate-90 whitespace-nowrap">VOICE ›</div></div>

        <!-- tap-to-start first-run overlay -->
        <div class="absolute inset-0 z-30 flex flex-col items-center justify-center gap-5 bg-background/55 backdrop-blur-[2px]" id="tap-start">
          <button data-tap id="play-ring" class="w-20 h-20 rounded-pill grid place-items-center border border-white/30 bg-white/5 press">
            ${icon("play", "translate-x-[2px]", 30)}
          </button>
          <div class="text-center px-10">
            <div class="font-sans text-[15px] font-semibold">Tap to start your briefing</div>
            <div class="font-mono text-[10px] text-white/50 mt-1.5 tracking-wide">AUDIO ON · WORD-BY-WORD CAPTIONS</div>
          </div>
        </div>
      </div>`);

    R = {
      layer,
      finite: layer.querySelector("#finite-bar"),
      counter: layer.querySelector("#counter"),
      headline: layer.querySelector("#headline-zone"),
      caption: layer.querySelector("#caption"),
      speaker: layer.querySelector("#speaker"),
      prog: layer.querySelector("#story-prog-fill"),
      actions: layer.querySelector("#action-row"),
      tapStart: layer.querySelector("#tap-start"),
      bg: layer.querySelector("#reel-bg"),
      peekR: layer.querySelector("#peek-r"),
      peekL: layer.querySelector("#peek-l"),
    };

    layer.querySelector("#btn-profile").addEventListener("click", (e) => { e.stopPropagation(); openProfile(); });
    layer.querySelector("#play-ring").addEventListener("click", (e) => { e.stopPropagation(); firstStart(); });

    attachGestures(layer);
    app.appendChild(layer);
    renderStory();
  }

  function renderFiniteBar() {
    R.finite.innerHTML = "";
    // compress 30 into ~24 segments for width; mark done/current
    const n = FEED_TOTAL;
    for (let i = 0; i < n; i++) {
      const d = document.createElement("div");
      d.className = "finite-seg" + (i < feedPos() ? " done" : i === feedPos() ? " current" : "");
      R.finite.appendChild(d);
    }
  }

  function renderStory() {
    const st = story();
    setAccent(seg(st).accent);
    if (R.bg) R.bg.style.backgroundImage = st.image ? `url('${st.image}')` : "none";
    renderFiniteBar();
    R.counter.textContent = String(feedPos() + 1).padStart(2, "0") + " / " + FEED_TOTAL;

    R.headline.innerHTML = `
      <div class="seg-chip mb-2.5" style="color:${seg(st).accent}"><span class="seg-dot"></span>${seg(st).label}</div>
      <h1 class="font-sans font-semibold text-[25px] leading-[1.12] tracking-[-0.02em] text-white max-w-[330px]" style="text-shadow:0 1px 18px rgba(2,6,23,.9)">${st.headline}</h1>`;

    renderActions();
    // reset caption
    S.curSentence = -1;
    renderCaptionSentence(0, true);
    R.prog.style.width = "0%";
  }

  function renderActions() {
    const st = story();
    const saved = S.saved.has(st.id);
    const followed = S.followed.has(st.id);
    const btn = (id, ic, label, cls = "", on = false) =>
      `<button data-tap class="flex flex-col items-center gap-1.5 press" id="${id}">
         <span class="act-btn ${cls} ${on ? (cls.includes('follow') ? 'follow-on' : 'on') : ''}">${icon(ic, "", 20)}</span>
         <span class="act-label">${label}</span>
       </button>`;
    R.actions.innerHTML =
      btn("act-save", "save", "Save", "", saved) +
      btn("act-share", "share", "Share", "") +
      btn("act-follow", followed ? "following" : "follow", "Follow", "follow", followed) +
      btn("act-ask", "ask", "Ask", "primary") +
      btn("act-voice", "voice", "Voice", "");

    R.actions.querySelector("#act-save").onclick = (e) => { e.stopPropagation(); S.saved.has(st.id) ? S.saved.delete(st.id) : S.saved.add(st.id); renderActions(); };
    R.actions.querySelector("#act-share").onclick = (e) => { e.stopPropagation(); toast("Link copied"); };
    R.actions.querySelector("#act-follow").onclick = (e) => { e.stopPropagation(); if (S.followed.has(st.id)) S.followed.delete(st.id); else { S.followed.add(st.id); toast("Following — we’ll flag new developments"); } renderActions(); };
    R.actions.querySelector("#act-ask").onclick = (e) => { e.stopPropagation(); openDetail(true); };
    R.actions.querySelector("#act-voice").onclick = (e) => { e.stopPropagation(); openVoice(); };
  }

  // ----- karaoke -----
  function renderCaptionSentence(si, dim) {
    const st = story();
    const sentence = st.captions[si];
    if (!sentence) return;
    R.caption.innerHTML = sentence.words
      .map((w, i) => `<span class="w ${w.hl ? "hl" : ""}" data-i="${i}">${w.t}</span>`)
      .join(" ");
    if (dim) [...R.caption.children].forEach((c) => c.classList.remove("spoken", "active"));
    // speaker alternates per sentence — each anchor keeps a fixed identity colour
    const sp = st.anchors[si % 2];
    const spc = { ALEX: "#6C8CFF", JORDAN: "#C792EA" }[sp] || "#9aa3b2";
    R.speaker.innerHTML = `<span class="seg-dot" style="background:${spc};box-shadow:0 0 12px ${spc}"></span>${sp}`;
    S.curSentence = si;
  }

  // Precompute sentence time boundaries (proportional to word count)
  function sentenceBounds() {
    const st = story();
    const counts = st.captions.map((s) => s.words.length);
    const total = counts.reduce((a, b) => a + b, 0);
    let acc = 0;
    return counts.map((c) => {
      const start = (acc / total) * S.duration;
      acc += c;
      const end = (acc / total) * S.duration;
      return { start, end, words: c };
    });
  }

  function tick(ts) {
    if (!S.playing) return;
    if (!S.lastTs) S.lastTs = ts;
    const dt = ts - S.lastTs;
    S.lastTs = ts;
    S.elapsed += dt;

    if (S.elapsed >= S.duration) { S.elapsed = S.duration; paintCaption(); next(); return; }
    paintCaption();
    R.prog.style.width = (S.elapsed / S.duration) * 100 + "%";
    S.raf = requestAnimationFrame(tick);
  }

  function paintCaption() {
    const bounds = sentenceBounds();
    let si = bounds.findIndex((b) => S.elapsed < b.end);
    if (si === -1) si = bounds.length - 1;
    if (si !== S.curSentence) renderCaptionSentence(si, true);
    const b = bounds[si];
    const within = Math.max(0, Math.min(1, (S.elapsed - b.start) / (b.end - b.start)));
    const active = Math.floor(within * b.words);
    [...R.caption.children].forEach((el, i) => {
      el.classList.toggle("spoken", i < active);
      el.classList.toggle("active", i === active);
    });
  }

  function play() {
    if (S.playing) return;
    S.playing = true;
    S.lastTs = 0;
    S.raf = requestAnimationFrame(tick);
  }
  function pause() {
    S.playing = false;
    cancelAnimationFrame(S.raf);
  }
  function togglePlay() { S.playing ? pause() : play(); }

  function firstStart() {
    S.audioUnlocked = true;
    R.tapStart.style.transition = "opacity 320ms ease";
    R.tapStart.style.opacity = "0";
    setTimeout(() => (R.tapStart.style.display = "none"), 320);
    if (!S.coachShown) { showCoach(); S.coachShown = true; }
    play();
  }

  function showCoach() {
    [R.peekR, R.peekL].forEach((p) => {
      p.style.transition = "opacity 500ms ease";
      p.style.opacity = "1";
      setTimeout(() => (p.style.opacity = "0"), 2600);
    });
  }

  // ----- nav between stories -----
  function swapStory(dir) {
    const inner = [R.headline, R.caption.parentElement];
    if (reduced) { renderStory(); resetPlayback(); return; }
    const off = dir > 0 ? -26 : 26;
    [R.headline, R.caption, R.speaker.parentElement.parentElement].forEach((el) => {
      el.style.transition = "transform 200ms ease, opacity 200ms ease";
      el.style.transform = `translateY(${off}px)`; el.style.opacity = "0";
    });
    setTimeout(() => {
      renderStory(); resetPlayback();
      [R.headline, R.caption, R.speaker.parentElement.parentElement].forEach((el) => {
        el.style.transition = "none";
        el.style.transform = `translateY(${-off}px)`; el.style.opacity = "0";
        void el.offsetWidth;
        el.style.transition = "transform 320ms cubic-bezier(.22,.61,.36,1), opacity 320ms ease";
        el.style.transform = "none"; el.style.opacity = "1";
      });
    }, 200);
  }

  function resetPlayback() { S.elapsed = 0; S.curSentence = -1; if (S.audioUnlocked) play(); }

  function next() {
    pause();
    if (S.idx >= STORIES.length - 1) { showCaughtUp(); return; }
    S.idx++; swapStory(1);
  }
  function prev() {
    if (S.idx <= 0) { bounce(); return; }
    pause(); S.idx--; swapStory(-1);
  }
  function bounce() {
    if (reduced) return;
    R.layer.animate([{ transform: "translateY(0)" }, { transform: "translateY(18px)" }, { transform: "translateY(0)" }], { duration: 320, easing: "ease-out" });
  }

  // ----- gestures -----
  function attachGestures(el) {
    let sx = 0, sy = 0, st0 = 0, active = false, decided = null;
    el.addEventListener("pointerdown", (e) => {
      if (e.target.closest("[data-tap]") || e.target.closest("#action-row")) return;
      active = true; decided = null; sx = e.clientX; sy = e.clientY; st0 = performance.now();
    });
    el.addEventListener("pointermove", (e) => {
      if (!active) return;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (!decided && (Math.abs(dx) > 10 || Math.abs(dy) > 10)) decided = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
    });
    el.addEventListener("pointerup", (e) => {
      if (!active) return; active = false;
      const dx = e.clientX - sx, dy = e.clientY - sy, dt = performance.now() - st0;
      const ax = Math.abs(dx), ay = Math.abs(dy);
      if (ax < 12 && ay < 12 && dt < 280) { if (S.audioUnlocked) togglePlay(); else firstStart(); return; }
      if (decided === "y" && ay > 56) { dy < 0 ? next() : prev(); }
      else if (decided === "x" && ax > 56) { dx > 0 ? openDetail() : openVoice(); }
    });
    el.addEventListener("pointercancel", () => (active = false));
  }

  // =====================================================================
  // STORY DETAIL (swipe right)
  // =====================================================================
  function openDetail(focusAsk) {
    if (S.lateral) return;
    pause();
    const st = story();
    const layer = buildDetail(st);
    app.appendChild(layer);
    device.classList.add("lateral-open");
    requestAnimationFrame(() => { layer.classList.add("open"); S.lateral = "detail"; });
    // staggered reveal
    const items = layer.querySelectorAll(".reveal");
    items.forEach((it, i) => setTimeout(() => it.classList.add("in"), 120 + i * 70));
    if (focusAsk) setTimeout(() => { layer.querySelector(".detail-scroll").scrollTo({ top: 99999, behavior: reduced ? "auto" : "smooth" }); layer.querySelector("#qa-input").focus(); }, 480);
  }
  function closeLateral() {
    const layer = app.querySelector(".layer-detail, .layer-voice");
    if (!layer) return;
    layer.classList.remove("open");
    device.classList.remove("lateral-open");
    S.lateral = null;
    setTimeout(() => layer.remove(), 440);
    if (S.audioUnlocked) play();
  }

  function biasBar(st) {
    const c = st.trust.coverage; const tot = c.left + c.center + c.right;
    const pct = (n) => (n / tot) * 100 + "%";
    return `
      <div class="bias-bar mb-2.5">
        <span style="width:${pct(c.left)};background:${BIAS.left}"></span>
        <span style="width:${pct(c.center)};background:${BIAS.center}"></span>
        <span style="width:${pct(c.right)};background:${BIAS.right}"></span>
      </div>
      <div class="flex justify-between font-mono text-[10px] text-white/55">
        <span style="color:${BIAS.left}">L · ${c.left}</span>
        <span style="color:${BIAS.center}">C · ${c.center}</span>
        <span style="color:${BIAS.right}">R · ${c.right}</span>
      </div>`;
  }

  function buildDetail(st) {
    const a = seg(st).accent;
    const layer = h(`
      <div class="layer layer-detail">
        <div class="detail-scroll flex-1 safe-bottom" style="padding-bottom:96px">
          <!-- header -->
          <div class="safe-top px-5 sticky top-0 z-20" style="background:linear-gradient(to bottom,#020617 72%,transparent);padding-top:62px;padding-bottom:14px">
            <div class="flex items-center justify-between">
              <button data-tap id="d-back" class="flex items-center gap-1.5 text-white/70 press">${icon("back", "", 20)}<span class="font-mono text-[11px] tracking-wide">REEL</span></button>
              <span class="seg-chip" style="color:${a}"><span class="seg-dot"></span>${seg(st).label}</span>
            </div>
          </div>

          <div class="px-5">
            <h1 class="reveal font-sans font-bold text-[27px] leading-[1.1] tracking-[-0.02em] mt-1">${st.headline}</h1>
            <p class="reveal font-sans text-[15px] leading-[1.5] text-white/55 mt-3">${st.dek}</p>
            <div class="reveal font-mono text-[10px] text-white/40 mt-3 tracking-wide">${st.outlet.toUpperCase()} · ${st.time} · &lt;100s READ</div>

            <!-- TRUST STRIP (compact, expandable) -->
            <div class="reveal trust-card mt-5 p-4">
              <div class="flex items-center justify-between gap-2 mb-3">
                <span class="font-mono text-[10px] tracking-[0.14em] text-white/55 whitespace-nowrap">COVERAGE</span>
                ${st.trust.blindspot ? `<span class="font-mono text-[9px] tracking-wide px-2 py-1 rounded-pill flex items-center gap-1 whitespace-nowrap" style="color:#E8B7BC;background:rgba(232,183,188,.1);border:1px solid rgba(232,183,188,.3)">${icon("blindspot", "", 12)}BLINDSPOT · ${st.trust.blindspot.toUpperCase()}</span>` : `<span class="font-mono text-[9px] tracking-wide px-2 py-1 rounded-pill text-white/40 whitespace-nowrap" style="border:1px solid rgba(255,255,255,.12)">BALANCED</span>`}
              </div>
              ${biasBar(st)}
              <div class="font-mono text-[10px] text-white/40 mt-2.5 tracking-wide">COVERED BY ${st.trust.outlet_count} OUTLETS</div>
              <button data-tap id="trust-toggle" class="w-full mt-3.5 pt-3.5 border-t border-white/10 flex items-center justify-between text-white/55 press">
                <span class="font-mono text-[10px] tracking-wide whitespace-nowrap">STORY TIMELINE</span>
                <span id="trust-chev" class="transition-transform">${icon("chevron-d", "", 16)}</span>
              </button>
              <div class="trust-drawer" id="trust-drawer">
                <div class="pt-4">
                  <div class="font-mono text-[10px] tracking-[0.14em] text-white/45 mb-3 flex items-center gap-1.5">${icon("clock", "", 13)}HOW IT DEVELOPED</div>
                  <div class="space-y-3 mb-1">
                    ${st.trust.timeline.map((t) => `<div class="tl-item"><span class="font-mono text-[10px] text-white/45">${t.when}</span><p class="font-sans text-[13px] text-white/75 leading-snug mt-0.5">${t.what}</p></div>`).join("")}
                  </div>
                </div>
              </div>
            </div>

            <!-- BODY (Playfair) -->
            <div class="detail-body reveal mt-7">
              ${st.detail_chunks.map((p) => `<p>${p}</p>`).join("")}
            </div>

            <!-- supporting visual: key figure -->
            <div class="reveal mt-1 mb-6 p-5" style="border:1px solid ${a}33;border-radius:1px;background:linear-gradient(135deg, ${a}14, transparent)">
              <div class="font-sans font-bold text-[40px] leading-none tracking-[-0.02em]" style="color:${a}">${st.keyFigure.value}</div>
              <div class="font-mono text-[10.5px] text-white/55 mt-2 tracking-wide">${st.keyFigure.label.toUpperCase()}</div>
            </div>

            <!-- opposing view: the one sparing light surface card -->
            <div class="reveal light-card p-5 mb-2">
              <div class="font-mono text-[10px] tracking-[0.14em] mb-2" style="color:#5b5e4f">↔ THE OPPOSING VIEW</div>
              <p class="lc-quote">${st.trust.opposing_view}</p>
            </div>
          </div>

          <!-- Q&A thread -->
          <div class="px-5 mt-6" id="qa-thread"></div>
        </div>

        <!-- pinned Q&A input -->
        <div class="absolute left-0 right-0 bottom-0 safe-bottom px-4 pt-3 z-30" style="background:linear-gradient(to top,#020617 60%,transparent)">
          <div class="flex gap-2 mb-2.5 overflow-x-auto pb-1" id="qa-chips" style="scrollbar-width:none"></div>
          <div class="flex items-center gap-2 bg-white/[0.05] rounded-control border border-white/12 pl-4 pr-2 py-2">
            <input id="qa-input" placeholder="Ask this story…" autocomplete="off"
              class="flex-1 bg-transparent outline-none font-sans text-[14px] placeholder:text-white/35 text-white" />
            <button data-tap id="qa-send" class="w-9 h-9 grid place-items-center rounded-control" style="background:rgba(59,130,246,.18);color:#93b4ff">${icon("send", "", 18)}</button>
          </div>
          <div class="font-mono text-[8.5px] text-white/30 text-center mt-2 tracking-wide">ANSWERS GROUNDED IN THIS STORY’S SOURCE</div>
        </div>
      </div>`);

    layer.querySelector("#d-back").onclick = closeLateral;
    const drawer = layer.querySelector("#trust-drawer");
    const chev = layer.querySelector("#trust-chev");
    layer.querySelector("#trust-toggle").onclick = () => { drawer.classList.toggle("open"); chev.style.transform = drawer.classList.contains("open") ? "rotate(180deg)" : ""; };

    // Q&A
    const chips = layer.querySelector("#qa-chips");
    st.suggested_questions.forEach((q) => {
      const c = h(`<button data-tap class="qa-chip press">${q}</button>`);
      c.onclick = () => askQuestion(st, q, layer);
      chips.appendChild(c);
    });
    const input = layer.querySelector("#qa-input");
    const send = () => { const v = input.value.trim(); if (!v) return; input.value = ""; askQuestion(st, v, layer); };
    layer.querySelector("#qa-send").onclick = send;
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

    // swipe-left from detail edge to go back
    attachBackSwipe(layer);
    return layer;
  }

  function attachBackSwipe(layer) {
    const sc = layer.querySelector(".detail-scroll");
    let sx = 0, sy = 0, on = false;
    layer.addEventListener("pointerdown", (e) => { if (e.target.closest("input,button,#qa-chips")) return; on = true; sx = e.clientX; sy = e.clientY; });
    layer.addEventListener("pointerup", (e) => {
      if (!on) return; on = false;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (dx > 70 && Math.abs(dy) < 50 && sc.scrollTop < 10) closeLateral();
    });
  }

  // ----- Q&A logic -----
  function askQuestion(st, q, layer) {
    const thread = layer.querySelector("#qa-thread");
    const qEl = h(`<div class="flex justify-end mb-3 fade-up"><div class="qa-bubble-q px-4 py-2.5 max-w-[78%]"><p class="font-sans text-[14px] leading-snug">${q}</p></div></div>`);
    thread.appendChild(qEl);
    const sc = layer.querySelector(".detail-scroll");
    sc.scrollTo({ top: 99999, behavior: reduced ? "auto" : "smooth" });

    const thinking = h(`<div class="flex justify-start mb-3"><div class="qa-bubble-a px-4 py-3"><span class="dot-typing"></span><span class="dot-typing"></span><span class="dot-typing"></span></div></div>`);
    thread.appendChild(thinking);
    sc.scrollTo({ top: 99999, behavior: reduced ? "auto" : "smooth" });

    setTimeout(() => {
      thinking.remove();
      const ans = resolveAnswer(st, q);
      let bubble;
      if (ans.grounded) {
        bubble = h(`<div class="flex justify-start mb-4 fade-up"><div class="qa-bubble-a px-4 py-3 max-w-[88%]">
          <p class="font-sans text-[14px] leading-relaxed text-white/90">${ans.text}</p>
          <div class="flex gap-1.5 mt-3 flex-wrap">${st.citations.map((c) => `<span class="cite-chip">${icon("spark", "", 11)}${c}</span>`).join("")}</div>
        </div></div>`);
      } else {
        bubble = h(`<div class="flex justify-start mb-4 fade-up"><div class="qa-refusal px-4 py-3 max-w-[90%]">
          <div class="font-mono text-[9px] tracking-[0.12em] mb-1.5" style="color:#E8B7BC">⌀ CAN’T ANSWER FROM SOURCE</div>
          <p class="font-sans text-[13.5px] leading-relaxed text-white/80">${ans.text}</p>
        </div></div>`);
      }
      thread.appendChild(bubble);
      sc.scrollTo({ top: 99999, behavior: reduced ? "auto" : "smooth" });
    }, reduced ? 350 : 1150);
  }

  function resolveAnswer(st, q) {
    // exact suggested match -> grounded canned answer
    if (st.answers[q]) return { grounded: true, text: st.answers[q] };
    const ql = q.toLowerCase();
    // grounded only if the question touches a curated topic word for THIS story
    const onTopic = (st.topics || []).some((t) => ql.includes(t));
    if (onTopic) {
      const qwords = ql.replace(/[^a-z0-9\s]/g, "").split(/\s+/).filter((w) => w.length > 3);
      let best = null, score = 0;
      Object.keys(st.answers).forEach((k) => {
        const s = qwords.filter((w) => k.toLowerCase().includes(w)).length;
        if (s > score) { score = s; best = k; }
      });
      return { grounded: true, text: st.answers[best || Object.keys(st.answers)[0]] };
    }
    // otherwise: the mandatory grounded-refusal
    return { grounded: false, text: "I can only answer from this story’s source — that isn’t covered here. Try one of the suggested questions, or ask about the details in this digest." };
  }

  // =====================================================================
  // VOICE MODE (swipe left)
  // =====================================================================
  function openVoice() {
    if (S.lateral) return;
    pause();
    const st = story();
    const granted = localStorage.getItem("n20-mic") === "1";
    const layer = h(`
      <div class="layer layer-voice items-center">
        <div class="ambient" style="opacity:.5"></div>
        <div class="reel-scrim"></div>
        <div class="safe-top w-full px-5 z-10 flex items-center justify-between" style="padding-top:62px">
          <button data-tap id="v-back" class="flex items-center gap-1.5 text-white/70 press">${icon("back", "", 20)}<span class="font-mono text-[11px] tracking-wide">REEL</span></button>
          <span class="font-mono text-[10px] tracking-[0.12em] text-white/45">VOICE · HANDS-FREE</span>
        </div>
        <div class="flex-1 w-full flex flex-col items-center justify-center z-10 px-8" id="v-body"></div>
        <div class="safe-bottom w-full px-6 z-10" id="v-foot"></div>
      </div>`);
    layer.querySelector("#v-back").onclick = closeLateral;
    app.appendChild(layer);
    device.classList.add("lateral-open");
    requestAnimationFrame(() => { layer.classList.add("open"); S.lateral = "voice"; });
    if (granted) voiceConversation(layer, st); else voicePermission(layer, st);
  }

  function voicePermission(layer, st) {
    const body = layer.querySelector("#v-body");
    body.innerHTML = `
      <div class="orb mb-9"><div class="orb-ring"></div></div>
      <h2 class="font-sans font-semibold text-[21px] text-center leading-tight w-full max-w-[300px]">Talk to this story,<br>hands-free.</h2>
      <p class="font-sans text-[14px] text-white/55 text-center mt-3 leading-relaxed w-full max-w-[300px]">Ask anything about “${st.headline.slice(0, 38)}…”. Answers stay grounded in the source. We only respond when you speak to it.</p>`;
    layer.querySelector("#v-foot").innerHTML = `
      <button data-tap id="mic-allow" class="w-full py-3.5 rounded-control font-sans font-semibold text-[15px] press" style="background:#3B82F6;color:#fff">Enable microphone</button>
      <button data-tap id="mic-deny" class="w-full py-3 mt-2 font-mono text-[11px] tracking-wide text-white/40 press">NOT NOW</button>
      <div class="font-mono text-[8.5px] text-white/25 text-center mt-2 tracking-wide">USES YOUR MIC ONLY WHILE THIS SCREEN IS OPEN</div>`;
    layer.querySelector("#mic-allow").onclick = () => { localStorage.setItem("n20-mic", "1"); voiceConversation(layer, st); };
    layer.querySelector("#mic-deny").onclick = () => voiceMicDenied(layer);
  }

  function voiceMicDenied(layer) {
    const body = layer.querySelector("#v-body");
    body.innerHTML = `
      <div class="w-16 h-16 rounded-pill grid place-items-center mb-6" style="border:1px solid rgba(232,183,188,.4);color:#E8B7BC">${icon("voice", "", 26)}</div>
      <h2 class="font-sans font-semibold text-[19px] text-center w-full max-w-[300px]">Voice needs the microphone</h2>
      <p class="font-sans text-[14px] text-white/55 text-center mt-3 leading-relaxed w-full max-w-[300px]">No problem — you can still read and ask by text. Enable the mic in Settings whenever you want hands-free.</p>`;
    layer.querySelector("#v-foot").innerHTML = `
      <button data-tap id="v-to-text" class="w-full py-3.5 rounded-control font-sans font-semibold text-[15px] press" style="background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15)">Read & ask by text instead</button>
      <button data-tap id="v-retry" class="w-full py-3 mt-2 font-mono text-[11px] tracking-wide text-white/40 press">TRY MICROPHONE AGAIN</button>`;
    layer.querySelector("#v-to-text").onclick = () => { closeLateral(); setTimeout(() => openDetail(true), 460); };
    layer.querySelector("#v-retry").onclick = () => voicePermission(layer, story());
  }

  function voiceConversation(layer, st) {
    const body = layer.querySelector("#v-body");
    body.innerHTML = `
      <div class="orb listening" id="orb"><div class="orb-ring"></div>
        <div class="absolute inset-0 flex items-center justify-center gap-[3px]" id="wave"></div>
      </div>
      <div class="font-mono text-[11px] tracking-[0.18em] mt-9 mb-1" id="v-status" style="color:${seg(st).accent}">LISTENING…</div>
      <p class="font-sans text-[15px] text-white/70 text-center mt-4 leading-relaxed min-h-[48px] w-full max-w-[310px]" id="v-transcript">“Try: what led to this?”</p>`;
    layer.querySelector("#v-foot").innerHTML = `<button data-tap id="v-end" class="w-full py-3 font-mono text-[11px] tracking-wide text-white/40 press">END VOICE · BACK TO REEL</button>`;
    layer.querySelector("#v-end").onclick = closeLateral;

    const wave = layer.querySelector("#wave");
    for (let i = 0; i < 5; i++) wave.appendChild(h(`<div class="wave-bar" style="height:${14 + (i % 3) * 10}px"></div>`));
    let waveTimer;
    const animWave = () => { if (reduced) return; [...wave.children].forEach((b) => (b.style.height = 10 + Math.random() * 34 + "px")); };

    const orb = layer.querySelector("#orb");
    const status = layer.querySelector("#v-status");
    const tr = layer.querySelector("#v-transcript");

    // scripted demo: listen -> user question -> respond (grounded) -> listen
    const seq = () => {
      if (!document.body.contains(layer)) { clearInterval(waveTimer); return; }
      orb.className = "orb listening"; status.textContent = "LISTENING…"; status.style.color = seg(st).accent;
      tr.textContent = "“Try: what led to this?”";
      waveTimer = setInterval(animWave, 120);
      setTimeout(() => {
        if (!document.body.contains(layer)) return clearInterval(waveTimer);
        tr.innerHTML = `<span class="text-white">“${st.suggested_questions[0]}”</span>`;
      }, 2600);
      setTimeout(() => {
        if (!document.body.contains(layer)) return clearInterval(waveTimer);
        clearInterval(waveTimer);
        orb.className = "orb responding"; status.textContent = "RESPONDING"; status.style.color = "#fff";
        const a = st.answers[st.suggested_questions[0]];
        tr.innerHTML = `<span class="text-white/85">${a}</span>`;
      }, 4200);
      setTimeout(() => { if (document.body.contains(layer)) seq(); }, 9500);
    };
    seq();
  }

  // =====================================================================
  // ONBOARDING
  // =====================================================================
  const INTERESTS = [
    { id: "geopolitics", label: "Geopolitics" }, { id: "markets", label: "Markets" },
    { id: "tech", label: "Tech & Science" }, { id: "sport", label: "Sport" },
    { id: "business", label: "Business" }, { id: "climate", label: "Climate" },
    { id: "culture", label: "Culture" }, { id: "health", label: "Health" },
  ];
  function startOnboarding() {
    const ov = h(`<div class="overlay-screen open" id="onb"></div>`);
    app.appendChild(ov);
    onbStep(ov, 0, new Set(["geopolitics", "tech"]));
  }
  function onbStep(ov, step, picks) {
    if (step === 0) {
      ov.innerHTML = `
        <div class="ambient"></div><div class="reel-scrim"></div>
        <div class="relative z-10 flex flex-col flex-1 safe-top safe-bottom px-7" style="padding-top:96px">
          <div class="mb-auto">${blipLogo(28, "glow")}</div>
          <div class="mb-auto">
            <h1 class="font-serif font-bold text-[44px] leading-[1.05] tracking-[-0.01em]">30 stories.<br>30 minutes.<br><span style="color:#FACC15">Caught up.</span></h1>
            <p class="font-sans text-[15.5px] text-white/60 leading-relaxed mt-5 max-w-[300px]">A finite daily briefing you actually finish — two AI anchors, sound-off captions, hands-free. Not a feed you fall into.</p>
          </div>
          <button data-tap id="onb-next" class="w-full py-4 rounded-control font-sans font-semibold text-[16px] press" style="background:#fff;color:#020617">Get started</button>
          <div class="font-mono text-[10px] text-white/35 text-center mt-3 tracking-wide">SWIPE UP/DOWN TO MOVE · RIGHT TO READ · LEFT TO ASK</div>
        </div>`;
      ov.querySelector("#onb-next").onclick = () => onbStep(ov, 1, picks);
    } else if (step === 1) {
      voiceProfileStep(ov, picks);
    } else {
      ov.innerHTML = `
        <div class="relative z-10 flex flex-col flex-1 safe-top safe-bottom px-7" style="padding-top:104px">
          <div class="font-mono text-[10px] tracking-[0.16em] text-white/40 mb-2">STEP 3 OF 3</div>
          <h1 class="font-serif font-bold text-[30px] leading-tight">Save your<br>briefing.</h1>
          <p class="font-sans text-[14px] text-white/55 mt-3 leading-relaxed max-w-[290px]">So your profile and follows are here tomorrow. We’ll email you a sign-in link — no password.</p>
          <div class="mt-7 w-full">
            <label class="font-mono text-[10px] tracking-[0.14em] text-white/40">EMAIL</label>
            <div class="flex items-center gap-2 bg-white/[0.05] rounded-control border border-white/12 px-4 py-3.5 mt-2">
              <input id="onb-email" type="email" placeholder="you@email.com" autocomplete="email" class="flex-1 bg-transparent outline-none font-sans text-[15px] placeholder:text-white/35 text-white" />
            </div>
            <div id="onb-email-err" class="font-mono text-[10px] text-white/0 mt-2 tracking-wide" style="color:#E8B7BC">&nbsp;</div>
          </div>
          <div class="mt-auto">
            <button data-tap id="onb-done" class="w-full py-4 rounded-control font-sans font-semibold text-[16px] press" style="background:#3B82F6;color:#fff">Continue with email</button>
            <div class="font-mono text-[10px] text-white/35 text-center mt-3 tracking-wide">NEW HERE OR RETURNING — SAME LINK</div>
          </div>
        </div>`;
      const email = ov.querySelector("#onb-email");
      const err = ov.querySelector("#onb-email-err");
      const finish = () => {
        const v = (email.value || "").trim();
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) { err.textContent = "Enter a valid email to continue"; err.style.color = "#E8B7BC"; email.focus(); return; }
        ov.classList.remove("open"); setTimeout(() => ov.remove(), 380);
        enterReelWithLoading();
      };
      ov.querySelector("#onb-done").onclick = finish;
      email.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(); });
    }
  }

  // =====================================================================
  // VOICE ONBOARDING — blip interviews the user and builds an interest profile.
  // Voice is primary (tap to answer); a typed chat is the optional fallback.
  // =====================================================================
  const VP_TURNS = [
    {
      q: "Hey — I’m blip. Skip the checkboxes. What do you actually want to keep up with?",
      user: "Mostly world news and tech… and I keep half an eye on the markets.",
      detect: [{ id: "geopolitics", label: "World & Politics" }, { id: "tech", label: "Tech & Science" }, { id: "markets", label: "Markets" }],
    },
    {
      q: "Got it. Big global headlines first, or your niche up top?",
      user: "Big stuff first — then my niche.",
      detect: [{ id: "world-first", label: "World-first", trait: true }],
    },
    {
      q: "Last thing — just the facts, or the why behind them?",
      user: "The why. I want to actually sound smart about it.",
      detect: [{ id: "context", label: "Context & “why”", trait: true }],
    },
  ];
  const VP_KW = {
    geopolitics: ["world", "politic", "geopolit", "war", "iran", "global", "election", "news"],
    markets: ["market", "stock", "econom", "finance", "money", "invest"],
    tech: ["tech", " ai", "science", "gadget", "startup", "comput"],
    sport: ["sport", "football", "nba", "soccer", "baseball", "game", "tennis"],
    business: ["business", "company", "ceo", "earning", "deal"],
    culture: ["culture", "art", "music", "film", "movie", "book"],
    climate: ["climate", "environment", "energy", "weather"],
    health: ["health", "medic", "wellness", "fitness"],
  };
  const VP_LABEL = { geopolitics: "World & Politics", markets: "Markets", tech: "Tech & Science", sport: "Sport", business: "Business", culture: "Culture", climate: "Climate", health: "Health" };

  function voiceProfileStep(ov, picks) {
    ov.innerHTML = `
      <div class="ambient" style="opacity:.35"></div><div class="reel-scrim"></div>
      <div class="relative z-10 flex flex-col flex-1 safe-top safe-bottom px-6" style="padding-top:74px">
        <div class="flex items-center justify-between">
          <span class="font-mono text-[10px] tracking-[0.16em] text-white/40">STEP 2 OF 3</span>
          <span class="font-mono text-[10px] tracking-wide text-white/40" id="vp-progress">QUESTION 1 / 3</span>
        </div>

        <div class="flex-1 flex flex-col items-center justify-center text-center">
          <div class="orb orb-brand listening" id="vp-orb" style="width:128px;height:128px">
            <div class="orb-ring"></div>
            <div class="absolute inset-0 flex items-center justify-center gap-[3px]" id="vp-wave"></div>
          </div>
          <div class="font-mono text-[10px] tracking-[0.18em] text-white/45 mt-6 mb-3" id="vp-state">BLIP IS ASKING…</div>
          <p class="font-sans font-medium text-[19px] leading-snug w-full max-w-[300px]" id="vp-q"></p>
          <p class="font-sans text-[14px] text-white/50 italic leading-snug w-full max-w-[300px] mt-3 min-h-[20px]" id="vp-you"></p>
        </div>

        <div class="mb-4">
          <div class="font-mono text-[9.5px] tracking-[0.12em] text-white/40 mb-2.5 flex items-center gap-1.5">${icon("spark", "", 12)}YOUR PROFILE \u00b7 TOP WORLD STORIES ALWAYS IN</div>
          <div class="flex flex-wrap gap-2 min-h-[34px]" id="vp-tags"><span class="font-sans text-[12.5px] text-white/30" id="vp-empty">Building as we talk…</span></div>
        </div>

        <div id="vp-controls" class="min-h-[112px] flex flex-col justify-end"></div>
      </div>`;

    const wave = ov.querySelector("#vp-wave");
    for (let i = 0; i < 5; i++) wave.appendChild(h(`<div class="wave-bar" style="height:${12 + (i % 3) * 9}px"></div>`));
    let waveTimer = null;
    const animWave = () => { if (reduced) return; [...wave.children].forEach((b) => (b.style.height = 9 + Math.random() * 30 + "px")); };
    const startWave = () => { if (waveTimer) return; waveTimer = setInterval(animWave, 130); };
    const stopWave = () => { clearInterval(waveTimer); waveTimer = null; [...wave.children].forEach((b, i) => (b.style.height = 12 + (i % 3) * 9 + "px")); };

    const orb = ov.querySelector("#vp-orb");
    const stateEl = ov.querySelector("#vp-state");
    const qEl = ov.querySelector("#vp-q");
    const youEl = ov.querySelector("#vp-you");
    const tagsEl = ov.querySelector("#vp-tags");
    const controls = ov.querySelector("#vp-controls");
    const progress = ov.querySelector("#vp-progress");
    const added = new Set();
    let i = 0, mode = "voice", busy = false;

    function addTags(list) {
      const empty = tagsEl.querySelector("#vp-empty"); if (empty) empty.remove();
      list.forEach((t, k) => {
        if (added.has(t.id)) return; added.add(t.id);
        if (!t.trait && VP_LABEL[t.id]) picks.add(t.id);
        const tag = h(`<span class="interest-tag"><span class="id"></span>${t.label}</span>`);
        tagsEl.appendChild(tag);
        setTimeout(() => tag.classList.add("in"), 60 + k * 90);
        setTimeout(() => tag.classList.add("lit"), 260 + k * 90);
      });
    }

    function renderControls() {
      if (i >= VP_TURNS.length) {
        controls.innerHTML = `<button data-tap id="vp-build" class="w-full py-4 rounded-control font-sans font-semibold text-[16px] press" style="background:#fff;color:#020617">Build my briefing</button>
          <div class="font-mono text-[10px] text-white/35 text-center mt-3 tracking-wide">${added.size} SIGNALS \u00b7 RETUNE ANYTIME IN SETTINGS</div>`;
        controls.querySelector("#vp-build").onclick = () => onbStep(ov, 2, picks);
        return;
      }
      if (mode === "voice") {
        controls.innerHTML = `<div class="flex flex-col items-center">
            <button data-tap id="vp-mic" class="w-[64px] h-[64px] rounded-pill grid place-items-center press" style="background:#fff;color:#020617">${icon("voice", "", 26)}</button>
            <div class="font-mono text-[10px] text-white/45 mt-3 tracking-[0.1em]">HOLD-FREE \u00b7 TAP TO ANSWER</div>
            <button data-tap id="vp-type" class="font-mono text-[10px] text-white/35 mt-2.5 tracking-wide press">or type instead</button>
          </div>`;
        controls.querySelector("#vp-mic").onclick = () => answer(VP_TURNS[i].user, VP_TURNS[i].detect);
        controls.querySelector("#vp-type").onclick = () => { mode = "type"; renderControls(); setTimeout(() => controls.querySelector("#vp-input")?.focus(), 50); };
      } else {
        controls.innerHTML = `<div class="flex items-center gap-2 bg-white/[0.05] rounded-control border border-white/12 pl-4 pr-2 py-2">
            <input id="vp-input" placeholder="Type your answer\u2026" autocomplete="off" class="flex-1 bg-transparent outline-none font-sans text-[14px] placeholder:text-white/35 text-white" />
            <button data-tap id="vp-send" class="w-9 h-9 grid place-items-center rounded-control" style="background:#3B82F6;color:#fff">${icon("send", "", 18)}</button>
          </div>
          <button data-tap id="vp-voice" class="font-mono text-[10px] text-white/35 mt-2.5 tracking-wide press block mx-auto">use voice instead</button>`;
        const inp = controls.querySelector("#vp-input");
        const send = () => { const v = inp.value.trim(); if (!v) return; const det = extract(v); answer(v, det.length ? det : VP_TURNS[i].detect); };
        controls.querySelector("#vp-send").onclick = send;
        inp.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
        controls.querySelector("#vp-voice").onclick = () => { mode = "voice"; renderControls(); };
      }
    }

    function extract(text) {
      const t = (" " + text.toLowerCase() + " ");
      const out = [];
      Object.keys(VP_KW).forEach((id) => { if (VP_KW[id].some((k) => t.includes(k))) out.push({ id, label: VP_LABEL[id] }); });
      return out;
    }

    function showQuestion() {
      busy = false;
      orb.className = "orb orb-brand listening";
      startWave();
      stateEl.textContent = "BLIP IS ASKING\u2026";
      progress.textContent = "QUESTION " + (i + 1) + " / " + VP_TURNS.length;
      youEl.textContent = "";
      typeText(qEl, VP_TURNS[i].q);
      setTimeout(() => { if (!busy) stateEl.textContent = "YOUR TURN \u2014 TAP TO ANSWER"; }, 1400);
      renderControls();
    }

    function answer(userText, detect) {
      if (busy) return; busy = true;
      stopWave();
      youEl.textContent = "\u201c" + userText + "\u201d";
      orb.className = "orb orb-brand responding";
      stateEl.textContent = "GOT IT \u2192";
      controls.innerHTML = "";
      addTags(detect);
      setTimeout(() => {
        i++;
        if (i >= VP_TURNS.length) {
          orb.className = "orb orb-brand";
          stateEl.textContent = "PROFILE READY";
          progress.textContent = "DONE";
          typeText(qEl, "Perfect — that’s your briefing tuned.");
          youEl.textContent = "";
          renderControls();
        } else {
          showQuestion();
        }
      }, reduced ? 500 : 1150);
    }

    // lightweight typewriter for the agent's question
    function typeText(el, text) {
      if (reduced) { el.textContent = text; return; }
      el.textContent = "";
      let n = 0;
      const iv = setInterval(() => { n++; el.textContent = text.slice(0, n); if (n >= text.length) clearInterval(iv); }, 18);
    }

    showQuestion();
  }


  function enterReelWithLoading() {
    const load = h(`
      <div class="overlay-screen open" id="loading" style="z-index:50">
        <div class="safe-top px-4" style="padding-top:62px">
          <div class="flex gap-[3px] mb-3">${Array.from({length:FEED_TOTAL}).map((_,i)=>`<div class="finite-seg ${i<FEED_START_INDEX?'done':''}"></div>`).join("")}</div>
          ${blipLogo(20)}
        </div>
        <div class="flex-1 flex flex-col justify-end px-5 safe-bottom" style="padding-bottom:120px">
          <div class="sk h-3 w-24 mb-4"></div>
          <div class="sk h-7 w-full mb-2"></div>
          <div class="sk h-7 w-3/4 mb-8"></div>
          <div class="sk h-9 w-full mb-2"></div>
          <div class="sk h-9 w-5/6"></div>
          <div class="font-mono text-[10px] text-white/35 mt-8 tracking-wide text-center">BUFFERING TODAY’S DIGEST…</div>
        </div>
      </div>`);
    app.appendChild(load);
    if (!S.booted) { mountReel(); S.booted = true; }
    setTimeout(() => { load.classList.remove("open"); setTimeout(() => load.remove(), 380); }, reduced ? 300 : 1100);
  }

  // =====================================================================
  // ALL CAUGHT UP (signature finish line)
  // =====================================================================
  function showCaughtUp() {
    const ov = h(`
      <div class="overlay-screen open" id="caughtup">
        <div class="ambient" style="opacity:.4"></div><div class="reel-scrim"></div>
        <div class="relative z-10 flex flex-col flex-1 items-center justify-center safe-top safe-bottom px-8">
          <div class="w-full max-w-[300px] mx-auto text-center">
          <div class="font-mono text-[12px] tracking-[0.2em] text-white/45 mb-6 fade-up">${FEED_TOTAL} / ${FEED_TOTAL} · DONE</div>
          <h1 class="font-serif font-bold text-[40px] leading-[1.08] fade-up">You’re all<br>caught up.</h1>
          <p class="font-sans text-[15px] text-white/55 mt-5 leading-relaxed fade-up">That’s the whole world today, in ${Math.round(STORIES.length * S.duration / 60000) + 28} minutes. No infinite scroll waiting — come back tomorrow.</p>
          <div class="w-12 h-px bg-white/15 my-7 fade-up"></div>
          <div class="fade-up w-full">
            <div class="font-mono text-[10px] tracking-[0.14em] text-white/40 mb-3 flex items-center justify-center gap-1.5">${icon("bell", "", 13)}WHILE YOU WERE OUT</div>
            <button data-tap id="cu-following" class="w-full trust-card p-3.5 flex items-center justify-between press text-left">
              <div><div class="font-sans text-[13.5px] font-medium">1 story you follow has an update</div><div class="font-mono text-[10px] text-white/45 mt-0.5">U.S. & IRAN · NEW DEVELOPMENT</div></div>
              <span style="color:#E8B7BC">${icon("back","rotate-180",18)}</span>
            </button>
          </div>
          <button data-tap id="cu-replay" class="mt-8 font-mono text-[11px] tracking-wide text-white/40 press fade-up">↻ REPLAY TODAY’S BRIEFING</button>
          </div>
        </div>
      </div>`);
    app.appendChild(ov);
    ov.querySelector("#cu-following").onclick = () => { ov.remove(); openFollowing(); };
    ov.querySelector("#cu-replay").onclick = () => { ov.remove(); S.idx = 0; renderStory(); resetPlayback(); };
  }

  // =====================================================================
  // PROFILE SHEET (discreet entry -> profile/following/settings + state nav)
  // =====================================================================
  function openProfile() {
    pause();
    const ov = h(`
      <div class="overlay-screen open" id="profile">
        <div class="flex flex-col flex-1 safe-top safe-bottom px-6" style="padding-top:72px">
          <div class="flex items-center justify-between mb-8">
            <h1 class="font-serif font-bold text-[26px]">You</h1>
            <button data-tap id="p-close" class="text-white/60 press">${icon("close","",24)}</button>
          </div>
          <div class="flex items-center gap-4 mb-8">
            <div class="w-14 h-14 rounded-pill grid place-items-center" style="background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12)">${icon("profile","",26)}</div>
            <div>
              <div class="font-sans font-semibold text-[17px]">Commuter</div>
              <div class="font-mono text-[11px] text-white/45 mt-0.5">12-DAY STREAK · ${FEED_TOTAL}/DAY</div>
            </div>
          </div>
          <div class="space-y-1" id="p-menu"></div>
          <div class="mt-auto font-mono text-[9px] text-white/25 tracking-wide text-center">PROTOTYPE · ALL SURFACES REACHABLE BELOW</div>
        </div>
      </div>`);
    app.appendChild(ov);
    ov.querySelector("#p-close").onclick = () => ov.remove();
    const menu = ov.querySelector("#p-menu");
    const row = (icn, label, sub, fn, demo) => {
      const r = h(`<button data-tap class="w-full flex items-center gap-3.5 py-3.5 px-1 press text-left border-b border-white/[0.06]">
        <span class="text-white/55">${icon(icn,"",20)}</span>
        <span class="flex-1"><span class="font-sans text-[15px] block">${label}</span>${sub?`<span class="font-mono text-[9.5px] text-white/35 tracking-wide">${sub}</span>`:""}</span>
        ${demo?'<span class="font-mono text-[8px] text-white/30 px-1.5 py-0.5 rounded-pill border border-white/15">DEMO</span>':""}
        <span class="text-white/25">${icon("back","rotate-180",16)}</span></button>`);
      r.onclick = () => { ov.remove(); fn(); };
      menu.appendChild(r);
    };
    row("bell", "Following", S.followed.size + " stories · 1 new update", openFollowing);
    row("save", "Saved", S.saved.size + " stories", () => toast("Saved list — " + S.saved.size + " items"));
    row("profile", "Replay onboarding", "First-run value prop", startOnboarding, true);
    row("voice", "Voice mode", "Hands-free Q&A", () => openVoice(), true);
    row("blindspot", "Connection error", "Offline / failed load state", showErrorScreen, true);
  }

  // =====================================================================
  // FOLLOWING / WHAT'S NEW
  // =====================================================================
  function openFollowing() {
    const followed = STORIES.filter((s) => S.followed.has(s.id));
    const ov = h(`
      <div class="overlay-screen open" id="following">
        <div class="detail-scroll flex-1 safe-bottom">
          <div class="safe-top px-6 sticky top-0 z-10" style="background:linear-gradient(to bottom,#020617 75%,transparent);padding-top:62px;padding-bottom:12px">
            <div class="flex items-center justify-between">
              <h1 class="font-serif font-bold text-[26px]">Following</h1>
              <button data-tap id="f-close" class="text-white/60 press">${icon("close","",24)}</button>
            </div>
            <p class="font-mono text-[10px] text-white/40 mt-1 tracking-wide">NEW SINCE YOU LAST WATCHED</p>
          </div>
          <div class="px-6 pt-2" id="f-list"></div>
        </div>
      </div>`);
    app.appendChild(ov);
    ov.querySelector("#f-close").onclick = () => ov.remove();
    const list = ov.querySelector("#f-list");
    if (!followed.length) {
      list.innerHTML = `<div class="text-center pt-20"><div class="font-sans text-[16px] text-white/55">Nothing followed yet</div><p class="font-mono text-[11px] text-white/35 mt-2 tracking-wide">FOLLOW A STORY TO TRACK UPDATES</p></div>`;
      return;
    }
    followed.forEach((st, i) => {
      const hasNew = i === 0; // seed: first followed has a new development
      const card = h(`<button data-tap class="w-full trust-card p-4 mb-3 text-left press fade-up">
        <div class="flex items-center justify-between mb-2">
          <span class="seg-chip" style="color:${seg(st).accent}"><span class="seg-dot"></span>${seg(st).label}</span>
          ${hasNew ? `<span class="font-mono text-[9px] tracking-wide px-2 py-0.5 rounded-pill" style="color:#FACC15;background:rgba(250,204,21,.12)">● NEW</span>` : `<span class="font-mono text-[9px] text-white/30 tracking-wide">NO CHANGE</span>`}
        </div>
        <div class="font-sans font-semibold text-[16px] leading-tight">${st.headline}</div>
        ${hasNew ? `<div class="mt-2.5 pt-2.5 border-t border-white/10 flex items-start gap-2"><span class="mt-1 w-1.5 h-1.5 rounded-pill flex-none" style="background:#FACC15"></span><p class="font-sans text-[13px] text-white/65 leading-snug">${st.trust.timeline[st.trust.timeline.length-1].what}</p></div>` : ""}
      </button>`);
      card.onclick = () => { ov.remove(); const ti = STORIES.indexOf(st); if (ti >= 0) { S.idx = ti; renderStory(); if (S.audioUnlocked) resetPlayback(); openDetail(); } };
      list.appendChild(card);
    });
  }

  // =====================================================================
  // ERROR SCREEN (offline / failed load — calm, on-brand)
  // =====================================================================
  function showErrorScreen() {
    const ov = h(`
      <div class="overlay-screen open" id="error">
        <div class="flex flex-col flex-1 items-center justify-center safe-top safe-bottom px-9 text-center">
          <div class="w-16 h-16 rounded-pill grid place-items-center mb-7" style="border:1px solid rgba(255,255,255,.18);color:rgba(255,255,255,.6)">${icon("blindspot","",26)}</div>
          <h1 class="font-serif font-bold text-[27px] leading-tight text-center w-full max-w-[300px]">Can’t reach<br>today’s briefing</h1>
          <p class="font-sans text-[14.5px] text-white/55 mt-4 leading-relaxed w-full max-w-[300px]">You appear to be offline. Your downloaded stories are still here — we’ll refresh the rest when you’re back.</p>
          <button data-tap id="e-retry" class="w-full max-w-[280px] py-3.5 rounded-control font-sans font-semibold text-[15px] mt-8 press" style="background:#fff;color:#020617">Retry</button>
          <button data-tap id="e-offline" class="w-full max-w-[280px] py-3 mt-2 font-mono text-[11px] tracking-wide text-white/40 press">CONTINUE WITH 5 DOWNLOADED</button>
        </div>
      </div>`);
    app.appendChild(ov);
    const close = () => ov.remove();
    ov.querySelector("#e-retry").onclick = () => { ov.querySelector("#e-retry").textContent = "Retrying…"; setTimeout(close, 900); };
    ov.querySelector("#e-offline").onclick = close;
  }

  // ----- toast -----
  let toastT;
  function toast(msg) {
    let t = document.getElementById("toast");
    if (!t) { t = h(`<div id="toast" class="absolute left-1/2 -translate-x-1/2 z-[100] px-4 py-2.5 rounded-pill font-mono text-[11px] tracking-wide" style="bottom:120px;background:rgba(255,255,255,.95);color:#020617;opacity:0;transition:opacity .25s,transform .25s"></div>`); device.querySelector("#screen").appendChild(t); }
    t.textContent = msg; requestAnimationFrame(() => { t.style.opacity = "1"; t.style.transform = "translateX(-50%) translateY(0)"; });
    clearTimeout(toastT); toastT = setTimeout(() => { t.style.opacity = "0"; }, 1800);
  }

  // ----- boot -----
  const now = new Date(); document.getElementById("sb-time").textContent = now.getHours() + ":" + String(now.getMinutes()).padStart(2, "0");
  startOnboarding();
})();
