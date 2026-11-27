---
id: R1
title: One owning group per task
kind: rule
applies_to: ["*"]
priority: 10
---
Each task has exactly one owning group, given as a single `group_id`. Group tasks by the verified owner in the repository or, for a proposed new service, the owner defined in the HLS. Never list two owners for one task; if work spans owners, split it into one task per owner.
