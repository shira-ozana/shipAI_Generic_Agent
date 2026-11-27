---
id: analyze-component
kind: skill
node: analyze_repository
priority: 50
---
# Skill: analyze a component

When to use: you receive source excerpts for one component of the repository.

Steps:
1. Read the inventory section to see where the component sits in the repository.
2. Find the entry points: routes, bootstrap files, exported modules, CLI or server
   start files. Cite the file and the identifier.
3. Describe the business behavior in user terms: what a user or caller can do,
   and what the component returns or shows.
4. List the interfaces: HTTP endpoints called or served, public functions,
   components, events, storage keys. Cite the code that defines or calls each one.
5. List the dependencies: other components, libraries, external services.
6. Describe the data flow from input to output in short steps.
7. Record what the excerpts do not show in `coverage_gaps`, and the paths you
   would need in `needs_more_evidence`.

Quality check: each finding has at least one evidence path. Every statement that
you did not read directly has `inferred: true`.
