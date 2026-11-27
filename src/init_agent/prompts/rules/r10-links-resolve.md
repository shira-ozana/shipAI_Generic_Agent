---
id: R10
title: Links must resolve
kind: rule
applies_to: ["draft_file", "repair_links"]
priority: 30
---
Every relative Markdown link must point to a file that exists after this run. Use
only the targets given in the "Link table" of the task, copied exactly: they are
already relative to the file being written. Any other file is named in `code`,
without a link. The caller checks every link, sends a file with a broken link back
once for repair, and turns links that are still broken into plain text.
