/**
 * The picker seed tree — LIFTED VERBATIM from `interest_picker.html`'s `DATA` const
 * (Phase 5 SP3). This is pure data, NOT re-authored: the object literal below is the
 * prototype's approved seed dataset transcribed unchanged, only typed.
 *
 * Kept in its own module (per the phase's permitted seed-tree split) so the engine in
 * `src/lib/followSets.ts` stays under the file-size limit. The engine imports
 * `RAW_PICKER_DATA` and transforms it into the typed §5 `PickerCategory[]` tree with
 * path-derived ids + registry pointers — see `liftPickerTree()`.
 *
 * The raw shape mirrors the prototype exactly: a node may carry `kind`/`ticker` and
 * its own nested `sets`; a set carries `items` and an optional `more` (the curated
 * extra rows the prototype's Show-more reveals — kept here as the OFFLINE fallback).
 */

import type { EntityKind } from "@/lib/entities";

/** A node in the raw lifted tree (prototype shape: label + optional kind/ticker/sets). */
export interface RawNode {
  label: string;
  kind?: EntityKind;
  ticker?: string;
  sets?: RawSet[];
}

/** A follow-set in the raw lifted tree (prototype shape: items + optional `more`). */
export interface RawSet {
  label: string;
  items: RawNode[];
  more?: RawNode[];
}

/** A subcategory in the raw lifted tree. */
export interface RawSubcategory {
  label: string;
  sets: RawSet[];
}

/** A top-level category in the raw lifted tree. */
export interface RawCategory {
  id: string;
  label: string;
  subs: RawSubcategory[];
}

/** The full raw lifted dataset (prototype `DATA`). */
export interface RawPickerData {
  meta: { name: string; version: string };
  categories: RawCategory[];
}

/**
 * The prototype's `DATA` const, transcribed unchanged. Source of truth:
 * `interest_picker.html` line 91. Do NOT re-author — re-lift if the prototype changes.
 */
export const RAW_PICKER_DATA: RawPickerData = {
  meta: { name: "Interest Picker", version: "2.0" },
  categories: [
    {
      id: "ai",
      label: "AI",
      subs: [
        {
          label: "Foundation models & LLMs",
          sets: [
            {
              label: "Labs & models",
              items: [
                { label: "OpenAI", kind: "org" },
                { label: "Anthropic", kind: "org" },
                { label: "Google DeepMind", kind: "org" },
                { label: "Meta AI", kind: "org" },
                { label: "Mistral", kind: "org" },
                { label: "xAI", kind: "org" },
                { label: "DeepSeek", kind: "org" },
              ],
            },
          ],
        },
        {
          label: "AI hardware & compute",
          sets: [
            {
              label: "Companies & topics",
              items: [
                { label: "Nvidia", ticker: "NVDA", kind: "company" },
                { label: "AMD", ticker: "AMD", kind: "company" },
                { label: "Broadcom", ticker: "AVGO", kind: "company" },
                { label: "TSMC", ticker: "TSM", kind: "company" },
                { label: "Data center buildout" },
                { label: "Compute energy demand" },
              ],
            },
          ],
        },
        {
          label: "Robotics & embodied AI",
          sets: [
            {
              label: "Topics & makers",
              items: [
                { label: "Humanoid robots" },
                { label: "Autonomous vehicles" },
                { label: "Drones" },
                { label: "Tesla Optimus", kind: "product" },
                { label: "Figure", kind: "company" },
                { label: "Waymo", kind: "company" },
              ],
            },
          ],
        },
        {
          label: "AI regulation & policy",
          sets: [
            {
              label: "Jurisdictions",
              items: [
                { label: "EU AI Act" },
                { label: "US policy" },
                { label: "China" },
                { label: "Global governance" },
              ],
            },
          ],
        },
        {
          label: "AI safety & alignment",
          sets: [
            {
              label: "Topics",
              items: [
                { label: "Alignment research" },
                { label: "Interpretability" },
                { label: "Evals & red-teaming" },
                { label: "Catastrophic risk" },
              ],
            },
          ],
        },
      ],
    },
    {
      id: "geopolitics",
      label: "Geopolitics",
      subs: [
        {
          label: "Armed conflict & war",
          sets: [
            {
              label: "Conflicts",
              items: [
                { label: "Ukraine–Russia", kind: "conflict" },
                { label: "Middle East", kind: "conflict" },
                { label: "African conflicts", kind: "conflict" },
                { label: "Asia-Pacific tensions", kind: "conflict" },
              ],
            },
          ],
        },
        {
          label: "Alliances & blocs",
          sets: [
            {
              label: "Organizations",
              items: [
                { label: "NATO", kind: "org" },
                { label: "European Union", kind: "org" },
                { label: "BRICS", kind: "org" },
                { label: "ASEAN", kind: "org" },
              ],
            },
          ],
        },
        {
          label: "Sanctions, tariffs & trade",
          sets: [
            {
              label: "Topics",
              items: [{ label: "Russia sanctions" }, { label: "China tariffs" }, { label: "Export controls (chips)" }],
            },
          ],
        },
        {
          label: "Energy geopolitics",
          sets: [
            {
              label: "Topics",
              items: [{ label: "Oil & OPEC" }, { label: "Natural gas & pipelines" }, { label: "Critical minerals" }],
            },
          ],
        },
      ],
    },
    {
      id: "business",
      label: "Business",
      subs: [
        {
          label: "Corporate news",
          sets: [
            {
              label: "What to track",
              items: [
                {
                  label: "Earnings",
                  sets: [
                    {
                      label: "Companies to track",
                      items: [
                        { label: "Apple", ticker: "AAPL", kind: "company" },
                        { label: "Microsoft", ticker: "MSFT", kind: "company" },
                        { label: "Nvidia", ticker: "NVDA", kind: "company" },
                        { label: "Amazon", ticker: "AMZN", kind: "company" },
                        { label: "Alphabet", ticker: "GOOGL", kind: "company" },
                        { label: "Meta", ticker: "META", kind: "company" },
                        { label: "Tesla", ticker: "TSLA", kind: "company" },
                        { label: "Eli Lilly", ticker: "LLY", kind: "company" },
                      ],
                      more: [
                        { label: "Berkshire Hathaway", ticker: "BRK.B", kind: "company" },
                        { label: "JPMorgan", ticker: "JPM", kind: "company" },
                        { label: "Visa", ticker: "V", kind: "company" },
                        { label: "Walmart", ticker: "WMT", kind: "company" },
                        { label: "Broadcom", ticker: "AVGO", kind: "company" },
                        { label: "UnitedHealth", ticker: "UNH", kind: "company" },
                        { label: "Exxon", ticker: "XOM", kind: "company" },
                        { label: "Costco", ticker: "COST", kind: "company" },
                      ],
                    },
                  ],
                },
                { label: "Mergers & acquisitions" },
                { label: "Leadership & executives" },
                { label: "IPOs" },
              ],
            },
          ],
        },
        {
          label: "Energy & commodities",
          sets: [
            {
              label: "Sectors",
              items: [
                {
                  label: "Oil & gas",
                  sets: [
                    {
                      label: "Majors",
                      items: [
                        { label: "ExxonMobil", ticker: "XOM", kind: "company" },
                        { label: "Chevron", ticker: "CVX", kind: "company" },
                        { label: "Shell", ticker: "SHEL", kind: "company" },
                        { label: "BP", ticker: "BP", kind: "company" },
                        { label: "ConocoPhillips", ticker: "COP", kind: "company" },
                        { label: "TotalEnergies", ticker: "TTE", kind: "company" },
                      ],
                    },
                    {
                      label: "Midstream & pipelines",
                      items: [
                        { label: "Kinder Morgan", ticker: "KMI", kind: "company" },
                        { label: "Enbridge", ticker: "ENB", kind: "company" },
                        { label: "Williams Companies", ticker: "WMB", kind: "company" },
                        { label: "Energy Transfer", ticker: "ET", kind: "company" },
                        { label: "TC Energy", ticker: "TRP", kind: "company" },
                        { label: "ONEOK", ticker: "OKE", kind: "company" },
                      ],
                    },
                    {
                      label: "Equipment, turbines & services",
                      items: [
                        { label: "GE Vernova", ticker: "GEV", kind: "company" },
                        { label: "Siemens Energy", ticker: "ENR.DE", kind: "company" },
                        { label: "Baker Hughes", ticker: "BKR", kind: "company" },
                        { label: "SLB (Schlumberger)", ticker: "SLB", kind: "company" },
                        { label: "Halliburton", ticker: "HAL", kind: "company" },
                        { label: "NOV Inc.", ticker: "NOV", kind: "company" },
                      ],
                    },
                  ],
                },
                { label: "Metals & mining" },
                { label: "Agricultural commodities" },
              ],
            },
          ],
        },
        {
          label: "Markets & investing",
          sets: [
            {
              label: "Asset classes",
              items: [
                { label: "Stocks & equities" },
                { label: "Bonds" },
                { label: "Major indices" },
                { label: "Commodities" },
                { label: "Currencies" },
              ],
            },
          ],
        },
        {
          label: "Macroeconomy",
          sets: [
            {
              label: "Indicators",
              items: [
                { label: "Inflation" },
                { label: "Interest rates & Fed" },
                { label: "Jobs" },
                { label: "GDP & growth" },
                { label: "Recession risk" },
              ],
            },
          ],
        },
        {
          label: "Crypto & fintech",
          sets: [
            {
              label: "Assets & topics",
              items: [
                { label: "Bitcoin", kind: "asset" },
                { label: "Ethereum", kind: "asset" },
                { label: "Solana", kind: "asset" },
                { label: "Stablecoins" },
                { label: "Coinbase", ticker: "COIN", kind: "company" },
              ],
            },
          ],
        },
      ],
    },
    {
      id: "environment",
      label: "Environment",
      subs: [
        {
          label: "Renewable energy & transition",
          sets: [
            {
              label: "Topics",
              items: [
                { label: "Solar" },
                { label: "Wind" },
                { label: "Nuclear" },
                { label: "Batteries & storage" },
                { label: "Electric vehicles" },
                { label: "Hydrogen" },
              ],
            },
          ],
        },
        {
          label: "Extreme weather & disasters",
          sets: [
            {
              label: "Topics",
              items: [
                { label: "Hurricanes" },
                { label: "Wildfires" },
                { label: "Floods" },
                { label: "Heatwaves & drought" },
                { label: "Earthquakes" },
              ],
            },
          ],
        },
        {
          label: "Climate science & policy",
          sets: [
            {
              label: "Topics",
              items: [
                { label: "Climate science" },
                { label: "COP summits" },
                { label: "Emissions targets" },
                { label: "Carbon markets" },
              ],
            },
          ],
        },
        {
          label: "Conservation & biodiversity",
          sets: [
            {
              label: "Topics",
              items: [{ label: "Endangered species" }, { label: "Forests & habitats" }, { label: "Oceans & coral" }],
            },
          ],
        },
      ],
    },
    {
      id: "politics",
      label: "Politics",
      subs: [
        {
          label: "Elections & campaigns",
          sets: [
            {
              label: "Topics",
              items: [
                { label: "National elections" },
                { label: "Legislative races" },
                { label: "State & local" },
                { label: "Campaigns" },
              ],
            },
          ],
        },
        {
          label: "Domestic policy",
          sets: [
            {
              label: "Issues",
              items: [
                { label: "Immigration" },
                { label: "Healthcare" },
                { label: "Taxes" },
                { label: "Education" },
                { label: "Guns" },
              ],
            },
          ],
        },
        {
          label: "Judiciary & courts",
          sets: [
            {
              label: "Topics",
              items: [{ label: "Supreme Court" }, { label: "Major rulings" }],
            },
          ],
        },
        {
          label: "Government & legislation",
          sets: [
            {
              label: "Topics",
              items: [{ label: "Executive branch" }, { label: "Legislation & bills" }, { label: "Budget" }],
            },
          ],
        },
      ],
    },
    {
      id: "tech",
      label: "Tech",
      subs: [
        {
          label: "Social media & platforms",
          sets: [
            {
              label: "Platforms",
              items: [
                { label: "X", kind: "company" },
                { label: "Meta", kind: "company" },
                { label: "TikTok", kind: "company" },
                { label: "YouTube", kind: "company" },
                { label: "Reddit", kind: "company" },
              ],
            },
          ],
        },
        {
          label: "Semiconductors & chips",
          sets: [
            {
              label: "Companies",
              items: [
                { label: "Nvidia", ticker: "NVDA", kind: "company" },
                { label: "TSMC", ticker: "TSM", kind: "company" },
                { label: "Intel", ticker: "INTC", kind: "company" },
                { label: "AMD", ticker: "AMD", kind: "company" },
                { label: "ASML", ticker: "ASML", kind: "company" },
              ],
            },
          ],
        },
        {
          label: "Space & aerospace",
          sets: [
            {
              label: "Players",
              items: [
                { label: "SpaceX", kind: "company" },
                { label: "NASA", kind: "org" },
                { label: "Blue Origin", kind: "company" },
                { label: "Rocket Lab", ticker: "RKLB", kind: "company" },
                { label: "Launches & missions" },
              ],
            },
          ],
        },
        {
          label: "Gaming",
          sets: [
            {
              label: "Topics & studios",
              items: [
                { label: "Consoles" },
                { label: "PC gaming" },
                { label: "Esports" },
                { label: "Nintendo", kind: "company" },
                { label: "Sony", kind: "company" },
              ],
            },
          ],
        },
        {
          label: "Cybersecurity",
          sets: [
            {
              label: "Topics",
              items: [{ label: "Data breaches" }, { label: "Ransomware" }, { label: "Vulnerabilities" }],
            },
          ],
        },
      ],
    },
    {
      id: "sport",
      label: "Sport",
      subs: [
        {
          label: "American football",
          sets: [
            {
              label: "Leagues",
              items: [
                {
                  label: "NFL",
                  kind: "league",
                  sets: [
                    {
                      label: "Teams you follow",
                      items: [
                        { label: "Kansas City Chiefs", kind: "team" },
                        { label: "Philadelphia Eagles", kind: "team" },
                        { label: "San Francisco 49ers", kind: "team" },
                        { label: "Dallas Cowboys", kind: "team" },
                        { label: "Buffalo Bills", kind: "team" },
                        { label: "Baltimore Ravens", kind: "team" },
                        { label: "Detroit Lions", kind: "team" },
                        { label: "Green Bay Packers", kind: "team" },
                      ],
                      more: [
                        { label: "Miami Dolphins", kind: "team" },
                        { label: "Pittsburgh Steelers", kind: "team" },
                        { label: "Cincinnati Bengals", kind: "team" },
                        { label: "Minnesota Vikings", kind: "team" },
                        { label: "Houston Texans", kind: "team" },
                        { label: "Los Angeles Rams", kind: "team" },
                        { label: "New York Jets", kind: "team" },
                        { label: "Seattle Seahawks", kind: "team" },
                      ],
                    },
                    {
                      label: "People to follow",
                      items: [
                        { label: "Patrick Mahomes", kind: "person" },
                        { label: "Josh Allen", kind: "person" },
                        { label: "Lamar Jackson", kind: "person" },
                        { label: "Jalen Hurts", kind: "person" },
                        { label: "Andy Reid", kind: "person" },
                        { label: "Saquon Barkley", kind: "person" },
                      ],
                    },
                  ],
                },
                {
                  label: "College football",
                  kind: "league",
                  sets: [
                    {
                      label: "Teams you follow",
                      items: [
                        { label: "Georgia", kind: "team" },
                        { label: "Michigan", kind: "team" },
                        { label: "Ohio State", kind: "team" },
                        { label: "Alabama", kind: "team" },
                        { label: "Texas", kind: "team" },
                        { label: "Oregon", kind: "team" },
                        { label: "Notre Dame", kind: "team" },
                        { label: "USC", kind: "team" },
                      ],
                      more: [
                        { label: "Penn State", kind: "team" },
                        { label: "LSU", kind: "team" },
                        { label: "Tennessee", kind: "team" },
                        { label: "Florida State", kind: "team" },
                        { label: "Oklahoma", kind: "team" },
                        { label: "Clemson", kind: "team" },
                      ],
                    },
                    {
                      label: "People to follow",
                      items: [
                        { label: "Kirby Smart", kind: "person" },
                        { label: "Ryan Day", kind: "person" },
                        { label: "Dabo Swinney", kind: "person" },
                        { label: "Deion Sanders", kind: "person" },
                      ],
                    },
                  ],
                },
              ],
            },
          ],
        },
        {
          label: "Basketball",
          sets: [
            {
              label: "Leagues",
              items: [
                {
                  label: "NBA",
                  kind: "league",
                  sets: [
                    {
                      label: "Teams you follow",
                      items: [
                        { label: "Boston Celtics", kind: "team" },
                        { label: "Los Angeles Lakers", kind: "team" },
                        { label: "Golden State Warriors", kind: "team" },
                        { label: "Denver Nuggets", kind: "team" },
                        { label: "Milwaukee Bucks", kind: "team" },
                        { label: "New York Knicks", kind: "team" },
                        { label: "Dallas Mavericks", kind: "team" },
                        { label: "Oklahoma City Thunder", kind: "team" },
                      ],
                      more: [
                        { label: "Miami Heat", kind: "team" },
                        { label: "Philadelphia 76ers", kind: "team" },
                        { label: "Phoenix Suns", kind: "team" },
                        { label: "Minnesota Timberwolves", kind: "team" },
                      ],
                    },
                    {
                      label: "People to follow",
                      items: [
                        { label: "LeBron James", kind: "person" },
                        { label: "Stephen Curry", kind: "person" },
                        { label: "Nikola Jokic", kind: "person" },
                        { label: "Luka Doncic", kind: "person" },
                        { label: "Giannis Antetokounmpo", kind: "person" },
                      ],
                    },
                  ],
                },
                {
                  label: "WNBA",
                  kind: "league",
                  sets: [
                    {
                      label: "Teams you follow",
                      items: [
                        { label: "Las Vegas Aces", kind: "team" },
                        { label: "New York Liberty", kind: "team" },
                        { label: "Indiana Fever", kind: "team" },
                        { label: "Connecticut Sun", kind: "team" },
                      ],
                    },
                    {
                      label: "People to follow",
                      items: [
                        { label: "Caitlin Clark", kind: "person" },
                        { label: "A'ja Wilson", kind: "person" },
                        { label: "Breanna Stewart", kind: "person" },
                        { label: "Angel Reese", kind: "person" },
                      ],
                    },
                  ],
                },
              ],
            },
          ],
        },
        {
          label: "Baseball",
          sets: [
            {
              label: "Leagues",
              items: [
                {
                  label: "MLB",
                  kind: "league",
                  sets: [
                    {
                      label: "Teams you follow",
                      items: [
                        { label: "Los Angeles Dodgers", kind: "team" },
                        { label: "New York Yankees", kind: "team" },
                        { label: "Atlanta Braves", kind: "team" },
                        { label: "Houston Astros", kind: "team" },
                        { label: "Philadelphia Phillies", kind: "team" },
                        { label: "New York Mets", kind: "team" },
                      ],
                    },
                    {
                      label: "People to follow",
                      items: [
                        { label: "Shohei Ohtani", kind: "person" },
                        { label: "Aaron Judge", kind: "person" },
                        { label: "Mookie Betts", kind: "person" },
                      ],
                    },
                  ],
                },
              ],
            },
          ],
        },
        {
          label: "Soccer",
          sets: [
            {
              label: "Leagues & competitions",
              items: [
                {
                  label: "Premier League",
                  kind: "league",
                  sets: [
                    {
                      label: "Teams you follow",
                      items: [
                        { label: "Manchester City", kind: "team" },
                        { label: "Arsenal", kind: "team" },
                        { label: "Liverpool", kind: "team" },
                        { label: "Manchester United", kind: "team" },
                        { label: "Chelsea", kind: "team" },
                        { label: "Tottenham", kind: "team" },
                      ],
                    },
                  ],
                },
                {
                  label: "La Liga",
                  kind: "league",
                  sets: [
                    {
                      label: "Teams you follow",
                      items: [
                        { label: "Real Madrid", kind: "team" },
                        { label: "Barcelona", kind: "team" },
                        { label: "Atlético Madrid", kind: "team" },
                      ],
                    },
                  ],
                },
                { label: "Champions League", kind: "league" },
              ],
            },
            {
              label: "People to follow",
              items: [
                { label: "Lionel Messi", kind: "person" },
                { label: "Cristiano Ronaldo", kind: "person" },
                { label: "Erling Haaland", kind: "person" },
                { label: "Kylian Mbappé", kind: "person" },
              ],
            },
          ],
        },
        {
          label: "Motorsport",
          sets: [
            {
              label: "Series",
              items: [
                {
                  label: "Formula 1",
                  kind: "league",
                  sets: [
                    {
                      label: "Teams you follow",
                      items: [
                        { label: "Red Bull", kind: "team" },
                        { label: "Ferrari", kind: "team" },
                        { label: "McLaren", kind: "team" },
                        { label: "Mercedes", kind: "team" },
                        { label: "Aston Martin", kind: "team" },
                      ],
                    },
                    {
                      label: "People to follow",
                      items: [
                        { label: "Max Verstappen", kind: "person" },
                        { label: "Lando Norris", kind: "person" },
                        { label: "Charles Leclerc", kind: "person" },
                        { label: "Lewis Hamilton", kind: "person" },
                      ],
                    },
                  ],
                },
              ],
            },
          ],
        },
        {
          label: "Tennis",
          sets: [
            {
              label: "Players",
              items: [
                { label: "Carlos Alcaraz", kind: "person" },
                { label: "Jannik Sinner", kind: "person" },
                { label: "Novak Djokovic", kind: "person" },
                { label: "Iga Swiatek", kind: "person" },
                { label: "Aryna Sabalenka", kind: "person" },
                { label: "Coco Gauff", kind: "person" },
              ],
            },
          ],
        },
        {
          label: "Cricket",
          sets: [
            {
              label: "Leagues",
              items: [
                {
                  label: "Indian Premier League (IPL)",
                  kind: "league",
                  sets: [
                    {
                      label: "Teams you follow",
                      items: [
                        { label: "Mumbai Indians", kind: "team" },
                        { label: "Chennai Super Kings", kind: "team" },
                        { label: "Royal Challengers Bengaluru", kind: "team" },
                        { label: "Kolkata Knight Riders", kind: "team" },
                        { label: "Gujarat Titans", kind: "team" },
                      ],
                    },
                  ],
                },
              ],
            },
            {
              label: "People to follow",
              items: [
                { label: "Virat Kohli", kind: "person" },
                { label: "Jasprit Bumrah", kind: "person" },
                { label: "Pat Cummins", kind: "person" },
                { label: "Joe Root", kind: "person" },
              ],
            },
          ],
        },
      ],
    },
    {
      id: "arts",
      label: "Arts",
      subs: [
        {
          label: "Music",
          sets: [
            {
              label: "Pick genres, then artists",
              items: [
                {
                  label: "Pop",
                  kind: "genre",
                  sets: [
                    {
                      label: "Artists & bands",
                      items: [
                        { label: "Taylor Swift", kind: "person" },
                        { label: "Billie Eilish", kind: "person" },
                        { label: "Sabrina Carpenter", kind: "person" },
                        { label: "Dua Lipa", kind: "person" },
                        { label: "Ariana Grande", kind: "person" },
                        { label: "Olivia Rodrigo", kind: "person" },
                      ],
                    },
                  ],
                },
                {
                  label: "Hip-hop & R&B",
                  kind: "genre",
                  sets: [
                    {
                      label: "Artists & bands",
                      items: [
                        { label: "Drake", kind: "person" },
                        { label: "Kendrick Lamar", kind: "person" },
                        { label: "SZA", kind: "person" },
                        { label: "Travis Scott", kind: "person" },
                        { label: "Doja Cat", kind: "person" },
                        { label: "Tyler, the Creator", kind: "person" },
                      ],
                    },
                  ],
                },
                {
                  label: "Rock & indie",
                  kind: "genre",
                  sets: [
                    {
                      label: "Artists & bands",
                      items: [
                        { label: "Coldplay", kind: "person" },
                        { label: "Arctic Monkeys", kind: "person" },
                        { label: "Foo Fighters", kind: "person" },
                        { label: "The 1975", kind: "person" },
                        { label: "Hozier", kind: "person" },
                      ],
                    },
                  ],
                },
                {
                  label: "Country",
                  kind: "genre",
                  sets: [
                    {
                      label: "Artists & bands",
                      items: [
                        { label: "Morgan Wallen", kind: "person" },
                        { label: "Zach Bryan", kind: "person" },
                        { label: "Luke Combs", kind: "person" },
                        { label: "Chris Stapleton", kind: "person" },
                      ],
                    },
                  ],
                },
                {
                  label: "Electronic",
                  kind: "genre",
                  sets: [
                    {
                      label: "Artists & bands",
                      items: [
                        { label: "Calvin Harris", kind: "person" },
                        { label: "Fred again..", kind: "person" },
                        { label: "Skrillex", kind: "person" },
                        { label: "ODESZA", kind: "person" },
                      ],
                    },
                  ],
                },
                {
                  label: "Latin",
                  kind: "genre",
                  sets: [
                    {
                      label: "Artists & bands",
                      items: [
                        { label: "Bad Bunny", kind: "person" },
                        { label: "Karol G", kind: "person" },
                        { label: "Peso Pluma", kind: "person" },
                        { label: "Shakira", kind: "person" },
                      ],
                    },
                  ],
                },
                {
                  label: "K-pop",
                  kind: "genre",
                  sets: [
                    {
                      label: "Artists & bands",
                      items: [
                        { label: "BTS", kind: "person" },
                        { label: "BLACKPINK", kind: "person" },
                        { label: "Stray Kids", kind: "person" },
                        { label: "NewJeans", kind: "person" },
                      ],
                    },
                  ],
                },
              ],
            },
          ],
        },
        {
          label: "Film & cinema",
          sets: [
            {
              label: "Follow",
              items: [
                { label: "Marvel / DC", kind: "franchise" },
                { label: "A24", kind: "company" },
                { label: "Oscars & Cannes", kind: "event" },
                { label: "Box office" },
                { label: "Christopher Nolan", kind: "person" },
              ],
            },
          ],
        },
        {
          label: "TV & streaming",
          sets: [
            {
              label: "Platforms & shows",
              items: [
                { label: "Netflix", kind: "company" },
                { label: "HBO / Max", kind: "company" },
                { label: "Disney+", kind: "company" },
                { label: "Apple TV+", kind: "company" },
              ],
            },
          ],
        },
        {
          label: "Books & literature",
          sets: [
            {
              label: "Topics",
              items: [{ label: "Fiction" }, { label: "Nonfiction" }, { label: "Prizes & bestsellers" }],
            },
          ],
        },
        {
          label: "Fashion",
          sets: [
            {
              label: "Houses & topics",
              items: [
                { label: "Gucci", kind: "brand" },
                { label: "Prada", kind: "brand" },
                { label: "Fashion weeks", kind: "event" },
                { label: "Trends" },
              ],
            },
          ],
        },
      ],
    },
  ],
};
