---
type: concept
up: "[[Home]]"
tags: [fixture, concept]
criticizes: "[[Concept-A]]"
source: "[[Source-X]]"
---

# Concept B

A fixture concept page. Criticises Concept-A and cites Source-X.

The materialization step should produce a `criticizedBy` edge from
Concept-A back to Concept-B, and a `supportedBy` edge from Concept-B
back to Concept-A (because Concept-A declares `supports: Concept-B`).
