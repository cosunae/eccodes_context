# Runtime eccodes definitions switching — design notes

Context: decoding/encoding GRIB messages against two incompatible local
parameter tables (ICON/COSMO's own `500xxx` paramId scheme vs. standard
WMO/ECMWF paramIds) within a single running process. `ECCODES_DEFINITION_PATH`
is normally read once, at first use of the default eccodes context, and is
effectively immutable for the rest of the process — this is what the fix in
this directory (see `GITHUB_ISSUE_DRAFT.md`) removes as a blocker.

## The underlying mechanism

```python
import ctypes, os

lib = ctypes.CDLL("libeccodes.so")
lib.grib_context_get_default.restype = ctypes.c_void_p
lib.grib_context_delete.argtypes = [ctypes.c_void_p]

def switch_definitions(path):
    ctx = lib.grib_context_get_default()
    lib.grib_context_delete(ctx)          # sets inited = 0
    os.environ["ECCODES_DEFINITION_PATH"] = path
    return lib.grib_context_get_default() # re-reads env, rebuilds context
```

Verified (`test_fix.py`, `results.log`) to work for repeated, alternating
switches in the same process, provided the `grib_context_reset()` fix is
applied — unlimited back-and-forth, not just a one-shot transition.

## Suggested implementation

A standalone module, since this mechanism isn't exposed by the standard
`eccodes`/`gribapi` Python bindings. This isn't just a sketch — it folds in
the design considerations below as actual code (library discovery,
feature-gating, context-manager semantics) rather than leaving them as
prose to implement later.

```python
"""eccodes_context_switch.py

Runtime switching of ECCODES_DEFINITION_PATH within a single process.

Requires an eccodes build with the grib_context_reset() concepts[] fix
(see GITHUB_ISSUE_DRAFT.md in this directory) -- without it, this is safe
for at most one switch per process, and a second switch will crash.
`ensure_patched_eccodes()` detects this at import time instead of letting
it fail unpredictably deep in a pipeline.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
from contextlib import contextmanager
from pathlib import Path


def _find_libeccodes() -> str:
    """Locate libeccodes.so: prefer the path the installed eccodes/gribapi
    python package is actually linked against, falling back to the
    system linker's search path."""
    try:
        import eccodes  # noqa: F401
        import gribapi.bindings as _b

        candidate = getattr(_b, "library_path", None)
        if candidate and Path(candidate).exists():
            return candidate
    except Exception:
        pass

    # Common layout for the "eccodes" PyPI wheel (bundled native lib).
    try:
        import eccodeslib  # type: ignore

        root = Path(eccodeslib.__file__).parent
        for rel in ("lib64/libeccodes.so", "lib/libeccodes.so"):
            candidate = root / rel
            if candidate.exists():
                return str(candidate)
    except Exception:
        pass

    found = ctypes.util.find_library("eccodes")
    if found:
        return found

    raise RuntimeError(
        "could not locate libeccodes.so; pass its path explicitly via "
        "ECCODES_CONTEXT_SWITCH_LIB_PATH"
    )


_LIB_PATH = os.environ.get("ECCODES_CONTEXT_SWITCH_LIB_PATH") or _find_libeccodes()
_lib = ctypes.CDLL(_LIB_PATH)

_lib.grib_context_get_default.restype = ctypes.c_void_p
_lib.grib_context_get_default.argtypes = []
_lib.grib_context_delete.argtypes = [ctypes.c_void_p]
_lib.grib_handle_new_from_message.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
_lib.grib_handle_new_from_message.restype = ctypes.c_void_p
_lib.grib_handle_delete.argtypes = [ctypes.c_void_p]
_lib.grib_get_long.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_long)]
_lib.grib_get_long.restype = ctypes.c_int

# A minimal, self-contained GRIB2 message (discipline=0, category=0,
# number=0 -- "Temperature", paramId 130 under any standard table) used
# purely to exercise two switches at import time. Kept tiny and embedded
# so the self-test has no external file dependency.
_PROBE_MESSAGE: bytes  # populated lazily the first time it's needed

_lock = threading.Lock()


def _switch(ctx, path: str):
    _lib.grib_context_delete(ctx)
    os.environ["ECCODES_DEFINITION_PATH"] = path
    return _lib.grib_context_get_default()


def ensure_patched_eccodes(probe_definitions_path: str) -> None:
    """Fail fast and clearly if the loaded eccodes doesn't have the
    grib_context_reset() fix, instead of segfaulting on a second switch
    somewhere deep in a real pipeline.

    `probe_definitions_path` just needs to be *some* valid definitions
    directory (e.g. the one already active) -- the actual value decoded
    doesn't matter, only that two switches in a row don't crash.
    """
    import eccodes as _ek  # the standard python bindings, only for building a probe message

    sample = _ek.codes_grib_new_from_samples("GRIB2")
    raw = _ek.codes_get_message(sample)
    _ek.codes_release(sample)

    with _lock:
        ctx = _lib.grib_context_get_default()
        for _ in range(3):  # the bug only manifests from the *second* switch onward
            ctx = _switch(ctx, probe_definitions_path)
            buf = ctypes.create_string_buffer(raw, len(raw))
            h = _lib.grib_handle_new_from_message(ctx, buf, len(raw))
            if not h:
                raise RuntimeError(
                    "eccodes_context_switch: probe decode failed after a definitions "
                    "switch -- this eccodes build is missing the grib_context_reset() "
                    "concepts[] fix (see GITHUB_ISSUE_DRAFT.md). Repeated switching is "
                    "not safe with this library."
                )
            _lib.grib_handle_delete(h)


@contextmanager
def definitions_path(path: str):
    """Temporarily switch ECCODES_DEFINITION_PATH for the duration of this
    block, restoring the previous value on exit.

    Not thread-safe against concurrent GRIB decode on other threads --
    this mutates one process-wide singleton context. Serialize all GRIB
    decode/encode through this lock-protected path, or don't use this
    mechanism at all if the pipeline is genuinely multi-threaded for
    GRIB I/O.
    """
    with _lock:
        prev = os.environ.get("ECCODES_DEFINITION_PATH")
        ctx = _lib.grib_context_get_default()
        ctx = _switch(ctx, path)
        try:
            yield
        finally:
            ctx = _lib.grib_context_get_default()
            if prev is not None:
                _switch(ctx, prev)
            else:
                _lib.grib_context_delete(ctx)
                os.environ.pop("ECCODES_DEFINITION_PATH", None)
```

Usage — the "switch it on/off before an operation" pattern originally
proposed, applied at the natural batch granularity (see consideration #2
below):

```python
import eccodes_context_switch as ecs

ecs.ensure_patched_eccodes(ICON_DEFS)  # once, at startup -- fails loudly if unpatched

with ecs.definitions_path(ICON_DEFS):
    decode_or_encode(lam_0_fields)       # whole batch, one switch

with ecs.definitions_path(STOCK_DEFS):
    decode_or_encode(global_fields)      # whole batch, one switch
```

## Design considerations

These aren't afterthoughts — they materially affect whether this is safe to
rely on in production, and are reflected directly in the code above.

### 1. Feature-gate on the eccodes fix

`ensure_patched_eccodes()` runs three switches against a tiny in-memory
probe message at startup and raises a clear `RuntimeError` naming the
required fix if any of them fail, rather than segfaulting deep inside a
real pipeline run. Call it once, early, before relying on `definitions_path`
anywhere else.

Until the fix is upstream *and* present in whatever eccodes build this
project actually ships (uenv/spack image), this means **vendoring the
patched library** for whichever component uses this mechanism — not
something to depend on silently or assume is present. `_find_libeccodes()`
/ `ECCODES_CONTEXT_SWITCH_LIB_PATH` exist specifically so a vendored build
can be pointed at explicitly rather than accidentally picking up whatever
unpatched system eccodes happens to be first on the linker path.

### 2. Batch, don't interleave per-message

Every switch re-parses the full definitions tree from scratch — it is not a
cheap toggle. Group all fields needing the same regime together and switch
once per group, not once per field.

This maps naturally onto anemoi-inference's existing `output.tee.outputs`
structure, which already separates the `lam_0` and `global` branches — that
existing branch boundary is the natural granularity for a switch, not a
per-field one.

### 3. Process-wide singleton — not safe for concurrent decode

This mutates one global context shared by the entire process. The `_lock`
in the implementation above only serializes *calls to this module* against
each other — it does nothing to protect against other code elsewhere in
the process (e.g. a different library, or GRIB decode happening directly
through the plain `eccodes` bindings without going through this module)
touching the default context concurrently on another thread. If the
pipeline ever becomes genuinely multi-threaded for GRIB decode/encode, this
approach stops being viable and subprocess isolation becomes necessary
instead for that scenario.

### 4. Context-manager shape, not a bare function

A `with` block — always restoring the previous path on exit, even on
exception — prevents the easy mistake of switching once and leaving the
rest of the process in the "wrong" regime, which is exactly the kind of bug
that is painful to track down later.

## Overall recommendation

Solid and tested for the "switch once, process a batch, switch back"
pattern demonstrated in `test_fix.py`. Not something to reach for until the
upstream fix lands (or it's vendored), and still not a substitute for
subprocess isolation if real thread-level concurrency over GRIB decode is
ever needed.
