Use this to split the feature into components.

1. Start from the existing components found during discovery. A component is a
   deployable or clearly separated part (API service, web frontend, worker,
   database, external integration), not a single class.
2. Map every functional requirement to the component(s) that implement it.
   A requirement with no component means a component is missing.
3. Give each component one clear responsibility and say what it does not own.
   Avoid splitting a small feature into many components.
4. Mark each component as new or changed, and describe the change.
5. Choose `architecture_path` values that mirror the repository layout (for
   example the folder that holds the component's code), lowercase,
   slash-separated.
6. Describe the dependencies between components and the direction of each call.
