Use this to understand the requirement before designing anything.

1. Restate the goal in one or two sentences: who the users are, what they can
   do after the feature ships, and why it matters.
2. Extract the functional requirements as short, testable statements
   ("A user can ..."). Give each one an ID: FR-1, FR-2, ...
3. Extract the non-functional requirements (performance, security, privacy,
   availability, scalability, accessibility, compliance). Give each an ID:
   NFR-1, NFR-2, ... Turn vague words into measurable targets when the text
   allows it ("fast" -> "p95 under 2 seconds"); otherwise flag them.
4. List constraints: technologies, integrations, deadlines or rules the
   design must respect, including ones implied by the existing codebase.
5. Find gaps and ambiguities: missing actors, undefined data, unclear error
   handling, unspecified limits, conflicting statements, edge cases
   (empty, large, concurrent, unauthorised, offline).
6. For each gap, either ask the user a clarifying question or state an
   explicit assumption. Ask only questions whose answer would change the
   design; keep them short, specific and answerable in one line. Prefer at
   most 5 questions, most important first.
