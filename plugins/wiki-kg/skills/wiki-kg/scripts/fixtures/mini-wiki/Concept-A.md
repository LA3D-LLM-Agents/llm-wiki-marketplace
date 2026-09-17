---
type: concept
up: "[[Home]]"
tags: [fixture, concept]
extends: "[[Concept-B]]"
supports: "[[Concept-B]]"
related: "[[Source-X]]"
---

# Concept A

A fixture concept page. It declares one frontmatter `extends` edge to
Concept-B, one `supports` edge to Concept-B, and one `related` edge to
Source-X.

Body mentions of [Concept B](Concept-B) ([*partOf*](Edge-Types#partOf))
and [Source X](Source-X) emerge as `mentions` edges from the extractor.
The Concept-B mention uses Variant 1 inline annotation so the extractor
also emits a `partOf` typed edge alongside the `mentions` edge.
