---
id: R10
title: Links must resolve
kind: rule
applies_to: ["draft_file"]
priority: 30
---
Use relative Markdown links only to files in the "planned harness files" list or
to repository paths in the inventory. Links from a harness file to repository code
must climb out of `{platform_dir}/` with `../`. The caller checks every link and
reports broken ones.
