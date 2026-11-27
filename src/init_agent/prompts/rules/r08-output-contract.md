---
id: R8
title: Output contract
kind: rule
applies_to: ["*"]
priority: 5
---
When a JSON schema is supplied, return exactly one JSON object inside a json code
fence and nothing else. When Markdown is requested, return only the file body:
no preamble, no closing remarks, no surrounding code fence.
