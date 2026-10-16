1. The context struct itself isn't the expensive part. grib_context_delete() + grib_context_get_default() (the delete/recreate cycle) takes ~0.01-0.3ms — that's not the bottleneck.

2. The real cost is the lazy concept/paramId table reparse on first decode after a switch, measured just now against the fixed build:

time
first decode right after a switch (cold)	65–120 ms
every subsequent decode on the same warm context	0.5–0.9 ms
So a switch is cheap, but it throws away the cached concept tables, and the next decode pays ~100-150x to rebuild them from disk. That's the expense you're sensing.

Could we keep two live contexts and just pick one per message, avoiding that cost entirely? I checked eccodes' internals for this:

grib_handle carries its own ->context pointer, and the actual paramId/concept/codetable resolution code (grib_concept.cc, grib_accessor_class_codetable.cc, grib_accessor_class_smart_table.cc) consistently uses that context — never falls back to the global default. So architecturally, if two independent, fully-warmed grib_context instances existed, decoding against each would correctly stay isolated and fast.
But that capability doesn't exist in current eccodes. There's a grib_context_new(parent) function — but it's entirely commented out in grib_context.cc (with the comment // Do we really need this?), not declared in any public header, and the sketch that remains doesn't even populate grib_definition_files_path or the concept arrays. There is exactly one process-wide context slot (the static default_grib_context); delete+get_default tear down and rebuild that same slot — they don't create a second one.
So: getting real dual-context reuse would mean reviving and finishing that dead code in eccodes itself — a genuine C++ feature addition (independent concepts/trie/definitions state per instance, thread-safety, auditing that nothing else silently assumes a single global), not a config flag.

Given the measured numbers, though — is this actually worth it for your use case? If you're switching once per batch of many messages (which is what DESIGN_NOTES.md already recommends — e.g. once per lam_0/global branch), the ~100ms cold cost amortizes away completely. It only matters if you're interleaving message-by-message between regimes, where you'd pay ~100ms per message. Which pattern matches your actual pipeline?
