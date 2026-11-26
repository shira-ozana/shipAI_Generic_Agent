---
id: assess-hooks
kind: skill
node: assess_automation
priority: 50
---
# Skill: assess hooks and verification

When to use: you receive the automation inventory detected by the caller (git hook
managers, package scripts, CI workflows, lint and format configs).

Steps:
1. Return each detected hook with `status: active` and its evidence path.
2. List the verification commands an agent should run after a change, from the
   narrowest (one test file) to the widest (full build). Status is `documented`
   unless the caller supplied an execution result.
3. Propose at most three new hooks that use commands that already exist in the
   repository. Each one has `status: proposed`, a trigger (for example
   `pre-commit`, `pre-push`, `agent: after edit`), the command, the scope, the
   evidence, and the failure behavior (block, warn, or report).
4. Put remarks about missing automation in `notes`.
