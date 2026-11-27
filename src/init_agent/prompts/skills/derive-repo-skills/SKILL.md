---
id: derive-repo-skills
kind: skill
node: derive_skills
priority: 50
---
# Skill: derive repository skills

When to use: you receive the component analysis, the test inventory, the commands,
the existing guidance and the shared skill catalog.

Steps:
1. Find workflows that repeat in the repository. Examples: add a feature page,
   add an API call to a service, add a unit test next to a file, run focused tests,
   add an end-to-end test.
2. For each workflow, check the shared skill catalog first. If a shared skill
   covers it, return `kind: shared_ref` with its `shared_skill_id`. Use `shared_ref`
   only with an id from "Shared skill catalog ids". A `SKILL.md` file inside this
   repository (for example one INIT wrote in an earlier run) is not a shared skill:
   return it as `local`, with the same name, when the evidence still supports it.
3. Return `kind: local` only when the repository shows a full example of the
   workflow. Fill `when_to_use`, `scope`, `prerequisites`, `steps`, `example`,
   `sources`, and `verification`.
4. When the workflow is likely but the example or the verification is missing,
   return `kind: needs_evidence` and fill `missing_evidence`.
5. Use kebab-case names that start with a verb.
