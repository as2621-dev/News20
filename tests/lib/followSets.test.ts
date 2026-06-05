import { describe, expect, it } from "vitest";
import type { EntityResult } from "@/lib/entities";
import {
  canonicalKeyFor,
  createSelectionStore,
  liftPickerTree,
  PICKER_TREE,
  selectionFromEntity,
  selectionFromFreeText,
  selectionFromNode,
  slug,
} from "@/lib/followSets";
import type { PickerFollowSet, PickerNode } from "@/types/picker";

/**
 * Engine tests for the recursive interest picker (Phase 5 SP3) — the lift, the
 * canonical-identity dedupe, and the subscribable selection store.
 *
 * Rule 9 — these encode WHY the behaviour matters, not just WHAT it does:
 *   - The lifted chip ids MUST equal SP1's registry `entity_id` scheme, or Show-more
 *     pagination + the persisted follow id silently point at the wrong row. A test
 *     pins the exact spec spot-check id, so a drift in `slug()` or the path scheme FAILS.
 *   - Cross-path dedupe is the spec §11 promise: the same real-world entity reached
 *     two ways is ONE follow with BOTH paths — not two follows (which would
 *     double-count + double-weight in ranking). A test asserts count==1 + both paths.
 *   - Preserve-on-collapse lives in the STORE, not the DOM: re-selecting a parent must
 *     restore child selections. The store-level test proves the state survives.
 */

/** Walk the lifted tree to a node by labels; throws if any segment is missing. */
function findNode(categoryId: string, ...labels: string[]): PickerNode {
  const category = PICKER_TREE.find((cat) => cat.id === categoryId);
  if (!category) {
    throw new Error(`category ${categoryId} not found`);
  }
  // First label addresses the subcategory; the rest descend set→item→set→item.
  const sub = category.subs.find((candidate) => candidate.label === labels[0]);
  if (!sub) {
    throw new Error(`subcategory ${labels[0]} not found`);
  }
  let sets: PickerFollowSet[] = sub.sets;
  let node: PickerNode | undefined;
  for (const label of labels.slice(1)) {
    node = undefined;
    for (const set of sets) {
      const match = set.items.find((item) => item.label === label);
      if (match) {
        node = match;
        break;
      }
    }
    if (!node) {
      throw new Error(`node ${label} not found among sets [${sets.map((s) => s.label).join(", ")}]`);
    }
    sets = node.sets ?? [];
  }
  if (!node) {
    throw new Error("no node resolved");
  }
  return node;
}

describe("slug — ported verbatim from the prototype (ids must match the registry)", () => {
  it("lowercases, collapses non-alphanumerics to one dash, and trims dashes", () => {
    // WHY: SP1's registry entity_id is built with this exact rule; any divergence
    // makes lifted chip ids miss their registry rows.
    expect(slug("Companies to track")).toBe("companies-to-track");
    expect(slug("Equipment, turbines & services")).toBe("equipment-turbines-services");
    expect(slug("Ukraine–Russia")).toBe("ukraine-russia"); // en-dash collapses
  });
});

describe("liftPickerTree — path-derived ids equal SP1's registry scheme (Rule 9)", () => {
  it("derives the exact spec spot-check id for Nvidia under Earnings", () => {
    // WHY: the spec pins this id. entity_id == entity_slug == this chip id, so the
    // persisted followId and listEntities({parent}) both depend on it being exact.
    const nvidia = findNode("business", "Corporate news", "Earnings", "Nvidia");
    expect(nvidia.id).toBe("business/corporate-news/what-to-track/earnings/companies-to-track/nvidia");
  });

  it("renders all 8 categories and no Health category (spec §3)", () => {
    const tree = liftPickerTree();
    expect(tree).toHaveLength(8);
    expect(tree.map((cat) => cat.id)).toEqual([
      "ai",
      "geopolitics",
      "business",
      "environment",
      "politics",
      "tech",
      "sport",
      "arts",
    ]);
    expect(tree.some((cat) => cat.label.toLowerCase() === "health")).toBe(false);
  });

  it("tags entities with kind and topics without (spec §2)", () => {
    // WHY: `type` gates the registry affordances; a mislabeled topic would wrongly
    // get Show-more/ticker, a mislabeled entity would lose them.
    const nvidia = findNode("business", "Corporate news", "Earnings", "Nvidia");
    expect(nvidia.type).toBe("entity");
    expect(nvidia.kind).toBe("company");
    expect(nvidia.ticker).toBe("NVDA");

    const inflation = findNode("business", "Macroeconomy", "Inflation");
    expect(inflation.type).toBe("topic");
    expect(inflation.kind).toBeUndefined();
    expect(inflation.ticker).toBeUndefined();
  });

  it("attaches a registry pointer to entity sets and the moreSeeds offline fallback", () => {
    // WHY: the registry pointer is what wires Show-more/Add-your-own; moreSeeds is the
    // offline fallback. The Earnings → Companies set is an entity set with `more`.
    const earnings = findNode("business", "Corporate news", "Earnings");
    const companiesSet = earnings.sets?.[0];
    expect(companiesSet?.registry).toEqual({
      parent: "business/corporate-news/what-to-track/earnings/companies-to-track",
      kind: "company",
    });
    expect(companiesSet?.moreSeeds?.length).toBe(8); // the 8 lifted `more` rows
  });

  it("leaves pure-topic sets WITHOUT a registry pointer (no Show-more/Add-your-own gate)", () => {
    // WHY: pure-topic sets must not call the entity registry — they have no entities.
    const ai = PICKER_TREE.find((cat) => cat.id === "ai");
    const safety = ai?.subs.find((sub) => sub.label === "AI safety & alignment");
    const topicsSet = safety?.sets[0];
    expect(topicsSet?.registry).toBeUndefined();
    expect(topicsSet?.items.every((item) => item.type === "topic")).toBe(true);
  });
});

describe("the four §4 marquee nested cases lift correctly (Rule 9)", () => {
  it("Earnings unfolds a Companies-to-track set with tickers + Show-more seeds", () => {
    const earnings = findNode("business", "Corporate news", "Earnings");
    expect(earnings.sets).toHaveLength(1);
    const companies = earnings.sets?.[0];
    expect(companies?.label).toBe("Companies to track");
    expect(companies?.items.every((item) => item.kind === "company")).toBe(true);
    expect(companies?.items.find((item) => item.label === "Apple")?.ticker).toBe("AAPL");
    expect(companies?.moreSeeds?.length).toBeGreaterThan(0);
  });

  it("Oil & gas unfolds exactly three sets (Majors / Midstream / Equipment)", () => {
    const oilGas = findNode("business", "Energy & commodities", "Oil & gas");
    expect(oilGas.sets?.map((set) => set.label)).toEqual([
      "Majors",
      "Midstream & pipelines",
      "Equipment, turbines & services",
    ]);
  });

  it("NFL unfolds BOTH a Teams set (with Show-more seeds) and a People set", () => {
    const nfl = findNode("sport", "American football", "NFL");
    expect(nfl.sets?.map((set) => set.label)).toEqual(["Teams you follow", "People to follow"]);
    const teams = nfl.sets?.[0];
    expect(teams?.registry?.kind).toBe("team");
    expect(teams?.moreSeeds?.length).toBeGreaterThan(0);
    expect(nfl.sets?.[1].registry?.kind).toBe("person");
  });

  it("College football unfolds the same shape INDEPENDENTLY of NFL", () => {
    // WHY: the cross-follow promise — each league owns its own sets, no special-casing.
    const college = findNode("sport", "American football", "College football");
    expect(college.sets?.map((set) => set.label)).toEqual(["Teams you follow", "People to follow"]);
    const nfl = findNode("sport", "American football", "NFL");
    expect(college.sets?.[0].id).not.toBe(nfl.sets?.[0].id); // distinct ids → independent
  });

  it("a Music genre unfolds its Artists & bands set", () => {
    const pop = findNode("arts", "Music", "Pop");
    expect(pop.kind).toBe("genre");
    expect(pop.sets?.[0].label).toBe("Artists & bands");
    expect(pop.sets?.[0].items.find((item) => item.label === "Taylor Swift")?.kind).toBe("person");
  });
});

describe("canonicalKeyFor — cross-path dedupe identity (Rule 9)", () => {
  it("gives the SAME key to one entity reached via different paths (ticker-keyed)", () => {
    // WHY: Nvidia under AI-hardware and under Earnings are different registry rows /
    // ids but ONE real-world follow → identical canonical key (`company:NVDA`).
    const nvidiaEarnings = findNode("business", "Corporate news", "Earnings", "Nvidia");
    const nvidiaAi = findNode("ai", "AI hardware & compute", "Nvidia");
    expect(nvidiaEarnings.id).not.toBe(nvidiaAi.id); // distinct path-derived ids
    const keyA = canonicalKeyFor({ type: "entity", kind: "company", ticker: "NVDA", label: "Nvidia" });
    const keyB = canonicalKeyFor({ type: "entity", kind: "company", ticker: "NVDA", label: "Nvidia" });
    expect(keyA).toBe("company:NVDA");
    expect(keyA).toBe(keyB);
  });

  it("falls back to slug(label) for a tickerless entity, and keys topics/freetext distinctly", () => {
    expect(canonicalKeyFor({ type: "entity", kind: "team", label: "Kansas City Chiefs" })).toBe(
      "team:kansas-city-chiefs",
    );
    expect(canonicalKeyFor({ type: "topic", label: "Inflation" })).toBe("topic:inflation");
    expect(canonicalKeyFor({ type: "entity", kind: "freetext", label: "My niche thing" })).toBe(
      "freetext:my-niche-thing",
    );
  });
});

describe("selection store — toggle, has, count, snapshot", () => {
  it("toggles a selection on and off, updating has()/count()", () => {
    const store = createSelectionStore();
    const inflation = findNode("business", "Macroeconomy", "Inflation");
    const selection = selectionFromNode({ node: inflation, path: ["Business", "Inflation"], source: "seed" });

    expect(store.toggle(selection)).toBe(true);
    expect(store.has(inflation.id)).toBe(true);
    expect(store.count()).toBe(1);

    expect(store.toggle(selection)).toBe(false);
    expect(store.has(inflation.id)).toBe(false);
    expect(store.count()).toBe(0);
  });

  it("notifies subscribers and changes snapshot identity on mutation", () => {
    const store = createSelectionStore();
    let notifications = 0;
    const unsubscribe = store.subscribe(() => {
      notifications += 1;
    });
    const before = store.getSnapshot();
    const inflation = findNode("business", "Macroeconomy", "Inflation");
    store.toggle(selectionFromNode({ node: inflation, path: ["Business", "Inflation"], source: "seed" }));
    const after = store.getSnapshot();

    expect(notifications).toBe(1);
    expect(after).not.toBe(before); // new identity → React re-renders
    expect(after).toHaveLength(1);
    unsubscribe();
  });

  it("CROSS-PATH DEDUPE: the same entity via two paths is ONE follow with BOTH paths", () => {
    // WHY (spec §11): selecting Nvidia under Earnings and again under AI-hardware must
    // NOT double-count — one canonical follow, two recorded paths. Double-counting
    // would double its ranking weight.
    const store = createSelectionStore();
    const nvidiaEarnings = findNode("business", "Corporate news", "Earnings", "Nvidia");
    const nvidiaAi = findNode("ai", "AI hardware & compute", "Nvidia");

    store.toggle(
      selectionFromNode({
        node: nvidiaEarnings,
        path: ["Business", "Corporate news", "Earnings", "Nvidia"],
        source: "seed",
      }),
    );
    store.toggle(
      selectionFromNode({
        node: nvidiaAi,
        path: ["AI", "AI hardware & compute", "Nvidia"],
        source: "seed",
      }),
    );

    expect(store.count()).toBe(1); // ONE canonical follow
    const all = store.all();
    expect(all).toHaveLength(1);
    const paths = [all[0].path, ...(all[0].extraPaths ?? [])];
    expect(paths).toContainEqual(["Business", "Corporate news", "Earnings", "Nvidia"]);
    expect(paths).toContainEqual(["AI", "AI hardware & compute", "Nvidia"]);
    // Both followIds resolve to the one canonical entry.
    expect(store.has(nvidiaEarnings.id)).toBe(true);
    expect(store.has(nvidiaAi.id)).toBe(true);
  });

  it("removing one of two dedupe paths keeps the canonical follow until the LAST is gone", () => {
    const store = createSelectionStore();
    const nvidiaEarnings = findNode("business", "Corporate news", "Earnings", "Nvidia");
    const nvidiaAi = findNode("ai", "AI hardware & compute", "Nvidia");
    const selEarnings = selectionFromNode({
      node: nvidiaEarnings,
      path: ["Business", "Corporate news", "Earnings", "Nvidia"],
      source: "seed",
    });
    const selAi = selectionFromNode({
      node: nvidiaAi,
      path: ["AI", "AI hardware & compute", "Nvidia"],
      source: "seed",
    });

    store.toggle(selEarnings);
    store.toggle(selAi);
    expect(store.count()).toBe(1);

    store.toggle(selEarnings); // remove one path
    expect(store.count()).toBe(1); // still followed via the AI path
    expect(store.has(nvidiaAi.id)).toBe(true);

    store.toggle(selAi); // remove the last path
    expect(store.count()).toBe(0); // now fully removed
  });

  it("stores a resolved registry entity (Show-more) and a free-text custom follow", () => {
    const store = createSelectionStore();
    const showMoreRow: EntityResult = { id: "x/jpmorgan", label: "JPMorgan", ticker: "JPM", kind: "company" };
    store.toggle(selectionFromEntity({ entity: showMoreRow, path: ["Business", "JPMorgan"], source: "more" }));

    const custom = selectionFromFreeText({ label: "Stripe", setId: "x", path: ["Business", "Stripe"] });
    store.toggle(custom);

    const all = store.all();
    expect(all.find((sel) => sel.label === "JPMorgan")?.source).toBe("more");
    const customSel = all.find((sel) => sel.label === "Stripe");
    expect(customSel?.source).toBe("custom");
    expect(customSel?.kind).toBe("freetext");
    expect(store.count()).toBe(2);
  });
});
