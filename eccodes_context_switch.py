from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading
from contextlib import contextmanager
from pathlib import Path


def _find_libeccodes() -> str:
    try:
        import eccodes  # noqa: F401
        import gribapi.bindings as _b

        candidate = getattr(_b, "library_path", None)
        if candidate and Path(candidate).exists():
            return candidate
    except Exception:
        pass

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


def _load(path: str) -> ctypes.CDLL:
    """Load libeccodes.so, reusing an already-loaded mapping of the same
    file if one exists (e.g. because the `eccodes` python package already
    imported it) instead of dlopen-ing it a second time. Re-opening an
    already-loaded native library independently can trigger unrelated
    duplicate-registration crashes in libraries it links against (seen in
    practice with eckit) -- RTLD_NOLOAD avoids that by returning the
    existing mapping instead of creating a new one."""
    try:
        return ctypes.CDLL(path, mode=os.RTLD_NOLOAD | ctypes.RTLD_GLOBAL)
    except OSError:
        return ctypes.CDLL(path)  # not already loaded -- a genuinely new library


_LIB_PATH = os.environ.get("ECCODES_CONTEXT_SWITCH_LIB_PATH") or _find_libeccodes()
_lib = _load(_LIB_PATH)

_lib.grib_context_get_default.restype = ctypes.c_void_p
_lib.grib_context_get_default.argtypes = []
_lib.grib_context_delete.argtypes = [ctypes.c_void_p]
_lib.grib_handle_new_from_message.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
_lib.grib_handle_new_from_message.restype = ctypes.c_void_p
_lib.grib_handle_delete.argtypes = [ctypes.c_void_p]
_lib.grib_get_long.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_long)]
_lib.grib_get_long.restype = ctypes.c_int

_lock = threading.Lock()


def _switch(ctx, path: str):
    _lib.grib_context_delete(ctx)
    os.environ["ECCODES_DEFINITION_PATH"] = path
    return _lib.grib_context_get_default()


def ensure_patched_eccodes(probe_definitions_path: str) -> None:
    import eccodes as _ek

    sample = _ek.codes_grib_new_from_samples("GRIB2")
    raw = _ek.codes_get_message(sample)
    _ek.codes_release(sample)

    with _lock:
        ctx = _lib.grib_context_get_default()
        for _ in range(3):
            ctx = _switch(ctx, probe_definitions_path)
            buf = ctypes.create_string_buffer(raw, len(raw))
            h = _lib.grib_handle_new_from_message(ctx, buf, len(raw))
            if not h:
                raise RuntimeError(
                    "eccodes_context_switch: probe decode failed after a definitions "
                    "switch -- this eccodes build is missing the grib_context_reset() "
                    "concepts[] fix. Repeated switching is not safe with this library."
                )
            _lib.grib_handle_delete(h)


@contextmanager
def definitions_path(path: str):
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
