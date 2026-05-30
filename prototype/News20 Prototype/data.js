/* News20 — mock data. 5 real digests from documents/m0-digests.md.
 * Captions are tokenized word-by-word; exactly ONE highlight keyword per sentence.
 * Trust numbers derived from one outlet->bias lookup (AllSides/Ad Fontes model).
 * Everything client-side; no backend. */

// Helper: turn a sentence string into word tokens, marking the [bracketed] keyword.
// e.g. cap("The U.S. hit another [target] inside Iran.")
function cap(str) {
  const words = str.split(/\s+/).map((w) => {
    const m = w.match(/^\[(.+?)\]([.,;:!?’”)]*)$/);
    if (m) return { t: m[1] + (m[2] || ""), hl: true };
    return { t: w, hl: false };
  });
  return { words };
}

const SEGMENTS = {
  geopolitics: { label: "Geopolitics", accent: "#EF4444" },
  markets:     { label: "Markets",     accent: "#22C55E" },
  tech:        { label: "Tech & Science", accent: "#22D3EE" },
  sport:       { label: "Sport",       accent: "#F59E0B" },
  wildcard:    { label: "Wildcard",    accent: "#E8B7BC" },
};

const STORIES = [
  {
    id: "s1",
    segment: "geopolitics",
    image: "assets/s1.png",
    headline: "U.S. strikes Iran again as Trump says a deal is “close”",
    outlet: "CNN",
    time: "08:10",
    dek: "Washington hits a second site inside Iran while Tehran tightens its grip on the Strait of Hormuz.",
    anchors: ["ALEX", "JORDAN"],
    captions: [
      cap("The U.S. military hit another [target] inside Iran overnight."),
      cap("Washington says the site [threatened] American forces and shipping."),
      cap("President Trump says a deal to end the fighting is [close]."),
      cap("But he’s not [satisfied] with the terms — and ready to strike again."),
      cap("Tehran just issued new rules for ships in the [Strait] of Hormuz."),
      cap("That chokepoint carries about a fifth of the world’s [oil]."),
      cap("A ceasefire talked up, a flashpoint heating up — which one [wins]?"),
    ],
    detail_chunks: [
      "The United States struck a second target inside Iran overnight, a site Washington says threatened American forces and commercial shipping in the Gulf. It is the latest escalation in a confrontation that has flared for weeks.",
      "President Trump told reporters he is confident a deal to end the fighting is close — but made clear he is not satisfied with the current terms, and is willing to restart strikes if Tehran does not meet U.S. demands.",
      "Meanwhile Iran is pushing back. It issued new rules for any vessel passing through the Strait of Hormuz, the narrow chokepoint where roughly a fifth of the world’s oil moves — an attempt to formalize control in defiance of U.S. warnings.",
    ],
    keyFigure: { value: "~20%", label: "of global oil transits Hormuz" },
    trust: {
      coverage: { left: 9, center: 7, right: 3 },
      outlet_count: 19,
      blindspot: "right",
      timeline: [
        { when: "08:10", what: "U.S. officials confirm an overnight strike inside Iran." },
        { when: "10:25", what: "Trump: a deal is “close,” but he won’t rush it." },
        { when: "13:40", what: "Iran issues new transit rules for the Strait of Hormuz." },
      ],
      opposing_view:
        "Some regional analysts argue the strikes harden Tehran’s position and make a negotiated deal less likely, not more.",
    },
    suggested_questions: ["What led to this?", "Why does Hormuz matter?", "Who’s affected?"],
    topics: ["iran","strike","hormuz","strait","oil","trump","deal","tehran","ceasefire","shipping","forces","nuclear","attack","gulf","target"],
    answers: {
      "What led to this?":
        "Weeks of escalating exchanges preceded the strike. The U.S. says this specific site posed a direct threat to its forces and to commercial shipping in the Gulf.",
      "Why does Hormuz matter?":
        "The Strait of Hormuz is the narrow passage where roughly a fifth of the world’s oil is shipped. New transit rules there can ripple into global energy prices.",
      "Who’s affected?":
        "U.S. forces in the region, Iran, and global shipping. Because Hormuz carries so much oil, the wider effect lands on energy markets and any country that imports through the Gulf.",
    },
    citations: ["CNN", "Reuters"],
  },

  {
    id: "s2",
    segment: "sport",
    image: "assets/s2.png",
    headline: "Travis Kelce buys a minority stake in the Cleveland Guardians",
    outlet: "ESPN",
    time: "09:02",
    dek: "The Chiefs tight end becomes part-owner of the baseball team he grew up watching.",
    anchors: ["JORDAN", "ALEX"],
    captions: [
      cap("Travis Kelce is now part-[owner] of a baseball team."),
      cap("The Chiefs star bought a minority stake in the [Guardians]."),
      cap("It’s the Cleveland team he grew up [watching]."),
      cap("As a kid he rode the train downtown to catch [games]."),
      cap("He joins stars like LeBron and Giannis buying into [sports]."),
      cap("The size of his stake? Still under [wraps]."),
      cap("Top athletes aren’t just playing the game — they’re [buying] it."),
    ],
    detail_chunks: [
      "Travis Kelce, the Kansas City Chiefs tight end, has purchased a minority stake in the Cleveland Guardians — the Major League Baseball team he grew up watching in his hometown of Cleveland Heights.",
      "It’s a homecoming story. As a kid, Kelce rode the light rail downtown with his dad to catch games; before football, he was one of the best baseball players in the Cleveland area.",
      "He joins a growing club of active stars taking ownership positions — LeBron James with the Red Sox, Giannis Antetokounmpo with the Brewers, and his own teammate Patrick Mahomes with the Royals. The size of Kelce’s stake hasn’t been disclosed.",
    ],
    keyFigure: { value: "undisclosed", label: "size of Kelce’s stake" },
    trust: {
      coverage: { left: 4, center: 11, right: 6 },
      outlet_count: 21,
      blindspot: null,
      timeline: [
        { when: "Mon", what: "Guardians confirm a new minority investor group." },
        { when: "Tue", what: "Reports identify Kelce among the investors." },
        { when: "Wed", what: "Kelce’s camp confirms; stake size withheld." },
      ],
      opposing_view:
        "Critics question whether celebrity minority stakes mean real influence, or are mostly a branding and access play.",
    },
    suggested_questions: ["How big is the stake?", "Which other athletes own teams?", "Why the Guardians?"],
    topics: ["kelce","guardian","cleveland","baseball","stake","owner","mlb","chiefs","mahomes","lebron","giannis","athlete","team","minority","sport"],
    answers: {
      "How big is the stake?":
        "It’s a minority stake, but the exact size hasn’t been disclosed by Kelce or the team.",
      "Which other athletes own teams?":
        "The story names LeBron James (Red Sox), Giannis Antetokounmpo (Brewers), and Patrick Mahomes (Royals) as active stars with ownership stakes.",
      "Why the Guardians?":
        "It’s Kelce’s hometown team. He grew up in Cleveland Heights watching them, and rode the light rail downtown with his dad to catch games as a kid.",
    },
    citations: ["ESPN", "AP"],
  },

  {
    id: "s3",
    segment: "tech",
    image: "assets/s3.png",
    headline: "Houston physicists break a 30-year superconductivity record",
    outlet: "ScienceDaily",
    time: "07:45",
    dek: "A team reaches 151 Kelvin at normal pressure — the highest ever recorded.",
    anchors: ["ALEX", "JORDAN"],
    captions: [
      cap("Physicists in Houston just broke a thirty-year [record]."),
      cap("It’s about superconductors — electricity with zero [resistance]."),
      cap("The catch has always been the extreme [cold]."),
      cap("The old mark, set in 1993, was 133 [Kelvin]."),
      cap("Houston pushed it to 151 — a new [high] at normal pressure."),
      cap("They used a trick called pressure-[quenching]."),
      cap("The someday payoff: lossless power [grids] and better scanners."),
      cap("A thirty-year wall, finally [moved]."),
    ],
    detail_chunks: [
      "Physicists at the University of Houston have broken a superconductivity record that stood for more than thirty years. Superconductors carry electricity with zero resistance — no energy lost at all.",
      "The long-standing catch is temperature: you needed extreme cold to make it work. The old record, set in 1993, was 133 Kelvin. The Houston team pushed that to 151 Kelvin — the highest ever achieved at normal, everyday pressure.",
      "Their method, called “pressure quenching,” squeezes the material and then locks in the new properties after the pressure is removed. It’s still about −122°C — but every degree closer to room temperature matters for lossless grids, faster electronics, fusion and medical scanners.",
    ],
    keyFigure: { value: "151 K", label: "new record (was 133 K, 1993)" },
    trust: {
      coverage: { left: 5, center: 14, right: 2 },
      outlet_count: 16,
      blindspot: "right",
      timeline: [
        { when: "1993", what: "Previous record set at 133 Kelvin." },
        { when: "May", what: "University of Houston reports 151 K at ambient pressure." },
        { when: "Now", what: "Result submitted for peer review and replication." },
      ],
      opposing_view:
        "Independent labs caution that the result needs replication before it reshapes the field — extraordinary claims need repeat measurement.",
    },
    suggested_questions: ["What is superconductivity?", "Why does pressure matter?", "When is this useful?"],
    topics: ["superconduct","kelvin","record","resistance","pressure","quench","houston","cold","temperature","grid","material","physic","electric","fusion"],
    answers: {
      "What is superconductivity?":
        "It’s when a material carries electricity with zero resistance — no energy is lost as heat. The hard part has always been that it only works at very low temperatures.",
      "Why does pressure matter?":
        "Many superconductors only work under crushing pressure. This record is notable because it reaches 151 K at normal, everyday pressure, using a “pressure quenching” trick to lock the properties in.",
      "When is this useful?":
        "Not immediately — it’s still around −122°C. But progress toward room temperature points to lossless power grids, faster electronics, better fusion and medical scanners down the line.",
    },
    citations: ["ScienceDaily", "Nature"],
  },

  {
    id: "s4",
    segment: "markets",
    image: "assets/s4.png",
    headline: "Nvidia’s blowout quarter — yet the stock slips",
    outlet: "CNBC",
    time: "06:30",
    dek: "Data-center revenue nearly doubles, but a stock priced for perfection dips anyway.",
    anchors: ["JORDAN", "ALEX"],
    captions: [
      cap("Nvidia reported earnings — and the AI boom isn’t [cooling]."),
      cap("Revenue hit 81.6 [billion] for the quarter."),
      cap("The data-center business nearly [doubled] from a year ago."),
      cap("Profit beat forecasts at a dollar [eighty-seven] a share."),
      cap("And they guided even [higher] for next quarter."),
      cap("Plus an 80-billion-dollar [buyback] and a fatter dividend."),
      cap("And yet — the stock actually [slipped]."),
      cap("Priced for perfection, even great isn’t always [enough]."),
    ],
    detail_chunks: [
      "Nvidia reported quarterly results that show no sign of the AI boom cooling. Revenue came in at $81.6 billion, ahead of the roughly $79 billion Wall Street expected.",
      "The engine is the data-center business, where revenue nearly doubled from a year ago — the AI gold rush captured in a single number. Profit beat too, at $1.87 a share against forecasts of $1.78, and the company guided to $91 billion next quarter.",
      "Nvidia also rewarded shareholders with an $80 billion buyback and a dividend hiked from a penny to 25 cents. And yet the stock slipped afterward — when you’re priced for perfection, even a blowout isn’t always good enough.",
    ],
    keyFigure: { value: "$81.6B", label: "quarterly revenue (est. ~$79B)" },
    trust: {
      coverage: { left: 6, center: 13, right: 9 },
      outlet_count: 24,
      blindspot: null,
      timeline: [
        { when: "16:05", what: "Nvidia releases Q1 results after the bell." },
        { when: "16:20", what: "Guidance of $91B next quarter beats estimates." },
        { when: "16:45", what: "Shares slip in after-hours trading." },
      ],
      opposing_view:
        "Bears argue the data-center surge is a one-off AI capex spike that won’t sustain the company’s valuation.",
    },
    suggested_questions: ["Why did the stock slip?", "How big is data center?", "What’s the guidance?"],
    topics: ["nvidia","stock","revenue","earning","data","center","chip","buyback","dividend","guidance","profit","share","quarter","valuation","billion","market"],
    answers: {
      "Why did the stock slip?":
        "Because expectations were sky-high. When a stock is “priced for perfection,” a beat that isn’t a blowout-of-the-blowout can still disappoint, and shares slipped in after-hours trading.",
      "How big is data center?":
        "Data-center revenue nearly doubled from a year ago — it’s the single biggest driver of the $81.6 billion total and the clearest read on AI demand.",
      "What’s the guidance?":
        "Nvidia guided to about $91 billion in revenue next quarter, comfortably above analyst estimates.",
    },
    citations: ["CNBC", "Kiplinger"],
  },

  {
    id: "s5",
    segment: "wildcard",
    image: "assets/s5.png",
    headline: "Pope Leo XIV issues his strongest warning yet on AI",
    outlet: "TechStartups",
    time: "11:20",
    dek: "A moral authority steps into a debate usually led by engineers and CEOs.",
    anchors: ["ALEX", "JORDAN"],
    captions: [
      cap("The Pope issued his strongest [warning] yet on AI."),
      cap("Pope Leo the Fourteenth urged world leaders to [slow] down."),
      cap("He wants international [safeguards] agreed upon."),
      cap("His fear: unchecked AI could deepen [misinformation]."),
      cap("And push autonomous [weapons] past human control."),
      cap("A moral voice entering a debate led by [engineers]."),
      cap("The same week a report named AI the top [cyber] threat."),
      cap("Two voices, one message: slow down before it gets [ahead]."),
    ],
    detail_chunks: [
      "Pope Leo XIV has issued one of his strongest warnings yet about artificial intelligence, urging world leaders to slow the race to deploy it and to agree on international safeguards.",
      "His concern is that unchecked AI could deepen misinformation, destabilize societies, and push autonomous weapons past meaningful human control — the last point being the line many quietly fear most.",
      "It’s a striking moment: a moral authority stepping into a debate usually led by engineers and CEOs. It lands the same week a major report said AI will soon be the single biggest force shaping global cybersecurity. Two very different voices, one message — slow down.",
    ],
    keyFigure: { value: "3 risks", label: "misinformation · instability · autonomous weapons" },
    trust: {
      coverage: { left: 8, center: 6, right: 4 },
      outlet_count: 14,
      blindspot: "right",
      timeline: [
        { when: "Mon", what: "Vatican publishes the Pope’s remarks on AI." },
        { when: "Tue", what: "Tech and policy figures respond." },
        { when: "Wed", what: "Cybersecurity report names AI the top emerging threat." },
      ],
      opposing_view:
        "Some technologists argue a deployment slowdown cedes ground to less cautious actors and delays beneficial uses of AI.",
    },
    suggested_questions: ["What did the Pope say?", "What risks are named?", "Who else is warning?"],
    topics: ["pope","leo","artificial","intelligence","safeguard","misinformation","weapon","autonomous","cyber","vatican","leader","regulation","warning","moral"],
    answers: {
      "What did the Pope say?":
        "Pope Leo XIV urged world leaders to slow the race to deploy AI and to agree on international safeguards, calling it one of his strongest interventions on the topic.",
      "What risks are named?":
        "Three: deepening misinformation, destabilizing societies, and pushing autonomous weapons past meaningful human control.",
      "Who else is warning?":
        "The story notes a major report the same week naming AI as soon to be the single biggest force shaping global cybersecurity — a secular voice landing on the same message.",
    },
    citations: ["TechStartups", "Vatican News"],
  },
];

// The feed shows ~30 stories; we have 5 detailed ones. We are at #7 of 30 (mid-briefing),
// so the finiteness counter reads believably. The 5 detailed stories are positions 7–11.
const FEED_TOTAL = 30;
// The 5 detailed stories occupy the last 5 of 30 so the finite "all caught up"
// finish line is reachable in-demo: counter reads 26/30 -> 30/30.
const FEED_START_INDEX = 25;

// Bias palette (project addition, design-language.md)
const BIAS = { left: "#3B82F6", center: "#A1A1AA", right: "#E8B7BC" };

window.NEWS20_DATA = { STORIES, SEGMENTS, FEED_TOTAL, FEED_START_INDEX, BIAS };
