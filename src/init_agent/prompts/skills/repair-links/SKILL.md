---
id: repair-links
kind: skill
node: repair_links
priority: 40
---
# Skill: repair broken links

When to use: a harness file was written and some of its relative links do not
resolve on disk. You receive the current body, the broken link targets, the
candidates found on disk, and the link table for this file.

Steps:
1. Return the full body of the file. Change only the broken links.
2. For each broken link:
   - when a candidate or a link-table entry clearly is the intended file (same
     name, a corrected skill name, a missing `../`), use that target exactly;
   - otherwise keep the label as plain text and remove the link.
3. Do not rewrite, shorten, reorder or summarize the other content. Do not add
   new links. Keep every heading and table.
4. Return only the Markdown body: no preamble, no code fence, no comment on the
   changes.
