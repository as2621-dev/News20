# Quickstart — the 5-minute version

This template gives Claude Code a set of slash commands that take you from
**"I have a vague idea"** all the way to **"working, reviewed code"** — without you
having to manage the messy middle.

You talk to it in plain English. It does the planning, building, and checking.

---

## What it does, in one picture

```
idea  →  /ideate  →  /brainstorm  →  /cto  →  /to-issues  →  /grab-issue  →  working code
        (optional)
```

Each command hands its output to the next one. You run them in order.

---

## The main commands (run these in order)

| Step | Command | What it does for you |
|---|---|---|
| 1 | `/ideate` | **Only if you have no idea yet.** Gives you a ranked shortlist of ideas to pick from. Skip if you already know what you want to build. |
| 2 | `/brainstorm` | Interviews you about your idea, pokes holes in it, and writes a clear product brief. It pushes back — it won't just agree with you. |
| 3 | `/cto` | Turns that brief into a real plan: what to build, the tech to use, and the steps. |
| 4 | `/to-issues` | Breaks the plan into small, build-able tasks and puts them on a to-do board (GitHub Issues). |
| 5 | `/grab-issue` | Picks the next task and **actually builds it** — writes the code, tests it, checks it, and saves it. Run it again for the next task. |

That's the whole loop. Most days you'll just keep running `/grab-issue`.

---

## How to use it (copy-paste)

In a fresh project made from this template, open Claude Code and type:

```
/brainstorm "a habit tracker that nags me on WhatsApp"
/cto
/to-issues
/grab-issue
```

Then keep building, one task at a time:

```
/grab-issue        # build the next task
/grab-issue        # build the one after that
```

Or let it drain the whole to-do list on its own:

```
/loop /grab-issue  # keeps grabbing and building until the board is empty
```

You only need a rough sentence to start. The commands ask you questions when they need more.

---

## Helper commands (use when you need them)

| Command | When to reach for it |
|---|---|
| `/commit` | Save your work as a tidy git commit. |
| `/debug` | A button/page in the browser is broken — it reproduces, fixes, and re-checks it. |
| `/rca` | Something broke and you want to know *why* before fixing. |
| `/codex` | You want a brutally honest second opinion that won't sugar-coat. |
| `/office-hours` | A weekly check-in: what's stuck, what's risky, what's next. |
| `/improve-architecture` | Every few days — tidies up the codebase as it grows. |
| `/handoff` | Wrapping up? Packs the conversation so a fresh chat can pick up where you left off. |
| `/compound` | Saves a useful lesson so future builds remember it. |

---

## The one rule to remember

**Go in order, and let each command finish.** Every command tells you the single next
step at the end of its run — just follow that. If you ever feel lost, run `/office-hours`
and it'll tell you where things stand.

---

Want the full details — every step inside `/grab-issue`, the folder layout, the kanban
board? See [`README.md`](./README.md).
