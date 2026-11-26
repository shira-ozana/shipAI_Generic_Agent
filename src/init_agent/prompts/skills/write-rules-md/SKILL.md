---
id: write-rules-md
kind: skill
node: draft_file
files: ["RULES.md", "GUIDELINES.md", "GUARDRAILS.md", "TESTING.md", "DEVELOPMENT.md", "TOOLS.md"]
priority: 50
---
# Skill: write RULES.md and other supporting files

`RULES.md` holds binding constraints only. Each rule has:
- an id (`RULE-01`, `RULE-02`, ...), a one-sentence rule, its scope (paths),
- its basis: the guidance file, contract, lint config or test that enforces it,
- how to check it (command or test).
A rule without a basis in the evidence belongs in `GUIDELINES.md` or in the gaps.

`GUIDELINES.md` holds recommendations: when to choose one valid pattern over
another, with an example path for each pattern.

`TESTING.md` and `DEVELOPMENT.md`: commands grouped by purpose, each marked
documented or executed, with the file it comes from.
