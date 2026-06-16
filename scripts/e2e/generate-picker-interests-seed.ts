/**
 * generate-picker-interests-seed — emit the `interests` taxonomy seed for every
 * TOPIC leaf in the picker tree (fix for `.agents/e2e/state/topic-persistence-rca.md`).
 *
 * Root cause being fixed: `persistPickerFollows` canonicalizes a topic selection's
 * LABEL against the `interests` table via an exact case-insensitive `ilike` on
 * `interest_label` / `interest_slug` (`src/lib/onboardingProfile.ts`
 * `findCanonicalInterest`). The live table only has the 18-row Phase 1e mini-seed,
 * and ZERO picker topic labels match — so every topic pick lands in `unpersisted`.
 *
 * This generator walks the lifted `PICKER_TREE` (`src/lib/followSets.ts`) exactly
 * the way the TopicTree builds selectable leaves (`src/lib/treeSelection.ts`
 * `buildSetItemNodes`):
 *   - an item WITH nested `sets` is a BRANCH (never selectable) → recurse, skip;
 *   - an item with `kind` is an ENTITY leaf → excluded (entities live in the 0007
 *     registry + `user_entity_follows`, NOT in `interests`);
 *   - an item with `type === "topic"` and no `sets` is a selectable TOPIC leaf →
 *     one `interests` row;
 *   - `moreSeeds` are folded in beside seed items as leaves, so they are walked too
 *     (today they are all entities, but the walk stays correct if that changes).
 *
 * Emitted rows (mirrors `supabase/seed/interests.sql` conventions):
 *   - `interest_label` = the EXACT picker label (all the `ilike` lookup needs);
 *   - `interest_slug`  = `<pickerCategoryId>.<slug(label)>` (namespaced, unique);
 *   - `depth_level`    = 1, parented under an EXISTING depth-0 row via a
 *     parent-slug subselect join (satisfies `ck_interest_depth`, no hardcoded UUIDs);
 *   - `on conflict (interest_slug) do nothing` → idempotent, never touches the
 *     existing 18 rows.
 *
 * Usage:
 *   npx tsx scripts/e2e/generate-picker-interests-seed.ts
 *   # writes supabase/seed/interests_picker_topics.sql
 */

import { writeFileSync } from "node:fs";
import path from "node:path";
import { PICKER_TREE, slug } from "@/lib/followSets";
import type { PickerFollowSet, PickerNode } from "@/types/picker";
import { REPO_ROOT } from "./env";

/** Where the generated idempotent seed SQL is written. */
const OUTPUT_SQL_PATH = path.join(REPO_ROOT, "supabase", "seed", "interests_picker_topics.sql");

/**
 * Picker category id → EXISTING depth-0 `interest_slug` (from
 * `supabase/seed/interests.sql`). Every picker category maps onto one of the 10
 * seeded depth-0 rows, so no new depth-0 row is needed and `ck_interest_depth`
 * (depth > 0 ⇒ parent NOT NULL) is satisfied by the subselect join.
 */
const PARENT_SLUG_BY_CATEGORY_ID: Readonly<Record<string, string>> = {
  ai: "tech", // AI → Tech & Science
  geopolitics: "world", // Geopolitics → World & Politics
  business: "business", // Business → Business & Markets
  environment: "climate", // Environment → Climate & Environment
  politics: "world", // Politics → World & Politics
  tech: "tech", // Tech → Tech & Science
  sport: "sport", // Sport → Sport
  arts: "entertainment", // Arts → Entertainment & Culture
};

/** One topic-leaf row destined for the `interests` table. */
interface TopicLeafRow {
  interest_slug: string;
  interest_label: string;
  parent_slug: string;
  interest_sort_order: number;
}

/**
 * Collect TOPIC leaves from a node list, mirroring `buildSetItemNodes`: an item
 * with nested `sets` is a branch (recurse into its sets, the item itself is NOT
 * selectable); an entity-kind item is excluded; a bare topic item is a leaf.
 */
function collectTopicLeavesFromItems(items: PickerNode[], collected: PickerNode[]): void {
  for (const item of items) {
    if (item.sets && item.sets.length > 0) {
      for (const childSet of item.sets) {
        collectTopicLeavesFromSet(childSet, collected);
      }
      continue; // Branch chip — not a selectable leaf in the TopicTree.
    }
    if (item.type === "topic") {
      collected.push(item);
    }
    // `type === "entity"` leaves are excluded — they live in the 0007 registry.
  }
}

/** Walk a follow-set's seed items + offline `moreSeeds` (both render as leaves). */
function collectTopicLeavesFromSet(followSet: PickerFollowSet, collected: PickerNode[]): void {
  collectTopicLeavesFromItems(followSet.items, collected);
  if (followSet.moreSeeds && followSet.moreSeeds.length > 0) {
    collectTopicLeavesFromItems(followSet.moreSeeds, collected);
  }
}

/** Escape a value for a single-quoted SQL string literal. */
function sqlQuote(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

function main(): void {
  const rows: TopicLeafRow[] = [];
  const seenSlugs = new Set<string>();
  const duplicateSlugs: string[] = [];

  for (const category of PICKER_TREE) {
    const parentSlug = PARENT_SLUG_BY_CATEGORY_ID[category.id];
    if (!parentSlug) {
      // Fail loud (Rule 12): an unmapped category would emit a row violating
      // ck_interest_depth (depth 1 needs a parent) — never skip silently.
      throw new Error(
        `No depth-0 parent slug mapped for picker category "${category.id}". ` +
          "fix_suggestion: add it to PARENT_SLUG_BY_CATEGORY_ID (must be an existing depth-0 interest_slug).",
      );
    }
    const categoryLeaves: PickerNode[] = [];
    for (const sub of category.subs) {
      for (const followSet of sub.sets) {
        collectTopicLeavesFromSet(followSet, categoryLeaves);
      }
    }
    let sortOrder = 10;
    for (const leaf of categoryLeaves) {
      const interestSlug = `${category.id}.${slug(leaf.label)}`;
      if (seenSlugs.has(interestSlug)) {
        duplicateSlugs.push(interestSlug);
        continue; // Same label twice under one category — one row is enough.
      }
      seenSlugs.add(interestSlug);
      rows.push({
        interest_slug: interestSlug,
        interest_label: leaf.label,
        parent_slug: parentSlug,
        interest_sort_order: sortOrder,
      });
      sortOrder += 10;
    }
  }

  const valuesSql = rows
    .map(
      (row) =>
        `  (${sqlQuote(row.interest_slug)}, ${sqlQuote(row.interest_label)}, ${sqlQuote(row.parent_slug)}, ${row.interest_sort_order})`,
    )
    .join(",\n");

  const seedSql = `-- Seed — interests taxonomy: picker TOPIC leaves (topic-persistence RCA fix, 2026-06-09)
--
-- GENERATED by scripts/e2e/generate-picker-interests-seed.ts — do not hand-edit;
-- re-run the generator if src/lib/pickerSeedTree.ts changes.
--
-- One depth-1 row per selectable TOPIC leaf in PICKER_TREE (${rows.length} leaves), so
-- persistPickerFollows' findCanonicalInterest (exact case-insensitive ilike on
-- interest_label / interest_slug) resolves every picker topic selection.
-- interest_label = the EXACT picker label; interest_slug is namespaced under the
-- picker category id. Parents resolve to the EXISTING depth-0 rows from
-- supabase/seed/interests.sql by slug subselect (no hardcoded UUIDs), satisfying
-- ck_interest_depth. Depth-1 rows leave interest_segment_slug NULL, matching the
-- existing depth-1 seed convention.
--
-- IDEMPOTENT: \`on conflict (interest_slug) do nothing\` — safe to re-run; never
-- modifies the existing 18 Phase 1e rows.

insert into interests (interest_slug, interest_label, depth_level, parent_interest_id, interest_sort_order)
select v.interest_slug, v.interest_label, 1, p.interest_id, v.interest_sort_order
from (values
${valuesSql}
) as v (interest_slug, interest_label, parent_slug, interest_sort_order)
join interests p on p.interest_slug = v.parent_slug
on conflict (interest_slug) do nothing;
`;

  writeFileSync(OUTPUT_SQL_PATH, seedSql, "utf8");
  console.log(
    JSON.stringify({
      event: "picker_interests_seed_generated",
      topic_leaf_count: rows.length,
      duplicate_slugs_skipped: duplicateSlugs,
      output_path: OUTPUT_SQL_PATH,
    }),
  );
}

main();
