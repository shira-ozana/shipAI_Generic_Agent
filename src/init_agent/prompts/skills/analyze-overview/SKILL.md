---
id: analyze-overview
kind: skill
node: analyze_overview
priority: 50
---
# Skill: repository overview

When to use: you receive the manifests, README, build configuration and existing
agent guidance of the repository.

Steps:
1. Write a `summary` of the system in two to four sentences: what it is, the main
   language and framework, and how it is structured.
2. List the commands for setup, start, build, lint and test in `commands`. Use the
   exact text from package scripts, README, Makefile or CI. Set `source` to the file.
   Status is `documented` unless an execution result is supplied.
3. Fill `project_context` with short string values that later agents need. Use
   these keys when the evidence supports them: `application`, `language`,
   `framework`, `package_manager`, `entry_point`, `routing`, `api_client`,
   `test_runner`, `e2e_runner`, `setup_hint`, `start_hint`, `build_hint`,
   `test_hint`. Add keys from the operator brief when it asks for specific facts.
4. Leave `components` empty. Components are analyzed in separate calls.
