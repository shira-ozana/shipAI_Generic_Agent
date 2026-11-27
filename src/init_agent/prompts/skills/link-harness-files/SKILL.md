---
id: link-harness-files
kind: skill
node: draft_file
priority: 40
---
# Skill: link harness files

When to use: every time you write the body of a harness file. The task gives you a
"Link table" built from the files that will exist on disk.

Steps:
1. Find the file you want to link to in the link table.
2. Copy its link target exactly as written. Do not shorten it, do not add or remove
   `../`, and do not change a file name or a skill name.
3. When the file you want is not in the link table, do not link it. Write its
   repository path in `code` instead, for example `src/app/app.routes.ts`.
4. Do not link to a file that the plan does not create, for example a
   `DEVELOPMENT.md` that is not in the table. Name the topic in plain text.
5. Links to the INIT report use the target given for `{platform_dir}/init/INIT.md`.

Check before you answer: every `](...)` target in your body appears in the link
table, or is an `https://` URL.
