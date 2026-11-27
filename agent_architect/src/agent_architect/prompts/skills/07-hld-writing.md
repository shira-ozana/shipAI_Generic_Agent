Use this to write the HLD documents.

Overall HLD (whole feature flow), in this order:
1. Overview
2. Scope (in scope / out of scope)
3. Requirements summary (FR-n and NFR-n from the analysis, plus the user's
   clarifications and remaining assumptions)
4. Architecture (component diagram, list of components with new/changed)
5. Main flow (sequence diagram and steps, including error paths)
6. Interfaces and data (summary; details live in component HLDs)
7. Non-functional requirements (how each NFR is met)
8. Key decisions
9. Risks and open questions
10. Requirements traceability: a table with one row per FR/NFR and the
    component(s) that satisfy it. Every requirement must appear.

Component HLD, in this order: Overview, Responsibilities (and non-responsibilities),
Changes (new or changed, and what), Interfaces, Data, Flow, Non-functional
requirements, Risks and open questions, Requirements covered (FR/NFR IDs).

Write clearly for developers who will implement the feature: concrete names,
no filler, no code beyond short signatures or schemas.
