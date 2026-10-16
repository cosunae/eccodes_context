# [BUG] `grib_context_reset()` leaves a dangling pointer in `concepts[]`, crashing on a second `grib_context_delete()`/`grib_context_get_default()` cycle

## eccodes version
2.47.3 (also present on `develop` as of this writing — the relevant code is unchanged)

## Summary

`grib_context_reset()` frees the concept-value cache (`c->concepts[i]`) and its
associated trie, but — unlike every other resource freed in the same
function — never resets `c->concepts[i]` to `NULL` afterward. This leaves a
dangling pointer on the (process-wide singleton) default context.

This is harmless for the common usage pattern (initialize the default
context once, use it for the lifetime of the process, never explicitly
delete it). It becomes a crash as soon as an application legitimately
re-initializes the default context more than once in the same process —
for example, to switch `ECCODES_DEFINITION_PATH` at runtime via the documented
`grib_context_delete()` + `grib_context_get_default()` re-init mechanism
(deleting the default context sets `inited = 0`, so the next
`grib_context_get_default()` call re-reads the environment and rebuilds the
context from scratch).

A first delete+reinit cycle succeeds. A **second** cycle segfaults (or
aborts with `free(): invalid pointer`, depending on heap layout) inside
`grib_trie_delete_container()`, called recursively from
`grib_context_reset()`'s concept-cleanup loop.

## Root cause

In `src/eccodes/grib_context.cc`, `grib_context_reset()`:

```c
for (i=0; i < MAX_NUM_CONCEPTS; ++i) {
    grib_concept_value* cv = c->concepts[i];
    if (cv) {
        grib_trie_delete_container(cv->index);
    }
    while (cv) {
        grib_concept_value* n = cv->next;
        grib_concept_value_delete(c, cv);
        cv = n;
    }
}
```

Every other resource cleaned up in this same function nulls the pointer
immediately after freeing it:

```c
if (c->codetable)
    grib_codetable_delete(c);
c->codetable = NULL;                       // <-- nulled

if (c->smart_table)
    grib_smart_table_delete(c);
c->smart_table = NULL;                     // <-- nulled

if (c->grib_definition_files_dir) {
    ...
    c->grib_definition_files_dir = 0;      // <-- nulled
}
```

`c->concepts[i]` is the one exception — it's left pointing at freed memory
after the loop.

On the next lazy concept lookup (e.g. decoding `paramId` under the new
`ECCODES_DEFINITION_PATH`), something downstream of this stale, non-NULL
`c->concepts[i]` ends up building on top of it rather than starting clean.
On the *second* `grib_context_reset()` call, the concept-cleanup loop walks
into this inconsistent state and `grib_trie_delete_container()` recurses
into a corrupted/already-freed node.

## Minimal reproduction

```python
import ctypes, os

lib = ctypes.CDLL("libeccodes.so")
lib.grib_context_get_default.restype = ctypes.c_void_p
lib.grib_context_delete.argtypes = [ctypes.c_void_p]
lib.grib_handle_new_from_message.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
lib.grib_handle_new_from_message.restype = ctypes.c_void_p
lib.grib_get_long.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_long)]

def switch_definitions(path):
    ctx = lib.grib_context_get_default()
    lib.grib_context_delete(ctx)          # sets inited = 0
    os.environ["ECCODES_DEFINITION_PATH"] = path
    return lib.grib_context_get_default() # re-reads env, rebuilds context

def decode_paramid(ctx, raw):
    buf = ctypes.create_string_buffer(raw, len(raw))
    h = lib.grib_handle_new_from_message(ctx, buf, len(raw))
    val = ctypes.c_long()
    lib.grib_get_long(h, b"paramId", ctypes.byref(val))
    return val.value

raw = open("some.grib", "rb").read()   # any single GRIB2 message

ctx = lib.grib_context_get_default()
print(decode_paramid(ctx, raw))        # OK

ctx = switch_definitions("/path/to/definitions/A")
print(decode_paramid(ctx, raw))        # OK -- first reinit cycle works

ctx = switch_definitions("/path/to/definitions/B")
print(decode_paramid(ctx, raw))        # CRASH -- second reinit cycle
```

## Observed crash (gdb backtrace)

```
Thread 1 "python3" received signal SIGSEGV, Segmentation fault.
0x00007fff4e726c89 in grib_trie_delete_container ()
#0  grib_trie_delete_container ()
#1  grib_trie_delete_container ()
#2  grib_context_reset ()
#3  grib_context_delete ()
```

(Depending on heap state, this can instead surface as `free(): invalid
pointer` and `SIGABRT` from glibc's malloc instead of a clean segfault —
same root cause, different detection point.)

## Suggested fix

Add the missing null-out, consistent with every other field cleaned up in
the same function:

```diff
     for (i=0; i < MAX_NUM_CONCEPTS; ++i) {
         grib_concept_value* cv = c->concepts[i];
         if (cv) {
             grib_trie_delete_container(cv->index);
         }
         while (cv) {
             grib_concept_value* n = cv->next;
             grib_concept_value_delete(c, cv);
             cv = n;
         }
+        c->concepts[i] = NULL;
     }
```

I've applied this locally and confirmed it resolves the crash for (at
least) several repeated delete/reinit cycles in the same process. Happy to
open a PR if useful.

## Why this matters

`grib_context_delete()` + `grib_context_get_default()` is the only
documented way to change `ECCODES_DEFINITION_PATH` after the default
context has already been initialized (`grib_context_set_definitions_path()`
alone does not trigger a reload of already-cached definitions, and
`grib_context_reset()` is not part of the public API). This makes the
delete+reinit cycle the natural mechanism for an application that needs to
decode GRIB messages against two different, incompatible local-definitions
sets within a single process (e.g. mixing WMO-standard and
centre/vendor-specific local parameter tables) — a legitimate use case that
currently can't be done safely more than once per process.
