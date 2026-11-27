# HLS-2: Event conventions

Events are named `<aggregate>.<past-tense-verb>`, carry JSON payloads, and are
described by a JSON Schema file in `contracts/`. Consumers must treat events as
at-least-once deliveries.
