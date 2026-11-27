---
id: R3
title: A dependency is a prerequisite to start
kind: rule
applies_to: ["*"]
priority: 30
---
Each entry in `depends_on` means: the named task's deliverable must exist before this task can start. Do not use dependencies for preferred order, shared topics, or loose relatedness. List direct prerequisites only, not transitive ones.
