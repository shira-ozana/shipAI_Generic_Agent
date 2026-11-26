---
id: write-architecture-ste100
kind: skill
node: draft_file
files: ["ARCHITECTURE.md", "DOMAIN.md", "CONTRACTS.md"]
priority: 50
---
# Skill: write ARCHITECTURE.md (ASD-STE100)

Sections, in this order:
1. `## System`: what the system does for its users. Two to four sentences.
2. `## Components`: one subsection per component with responsibility, entry
   points, interfaces, dependencies and evidence paths.
3. `## Data flow`: numbered steps for the main user flows, from input to output.
4. `## External interfaces`: APIs the system calls or serves, with the code that
   defines them.
5. `## Gaps`: what the evidence did not show.

Writing: one topic per sentence, maximum 25 words, active voice, simple present
tense, one term per concept. Keep paths and identifiers exactly as in the code.
Put evidence as relative links or code spans at the end of the sentence or item.
Mark inferred statements with `(inferred)`.
