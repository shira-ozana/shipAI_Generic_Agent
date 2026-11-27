---
id: G3
title: Expose what can start in parallel
kind: guideline
applies_to: ["*"]
priority: 30
---
After mapping dependencies, check which tasks have no prerequisites or share the same prerequisites. Do not add dependencies that hide this: a plan with fewer, direct prerequisites lets the reviewer see which work can run in parallel.
