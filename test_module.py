import os
import sys

_RESULTS_FILE = "/scratch/mch/cosuna/runml-tmp-test/eccodes_context/module_test_results.log"


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    with open(_RESULTS_FILE, "a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


sys.path.insert(0, "/scratch/mch/cosuna/runml-tmp-test/eccodes_context")

STOCK_DEFS = "/user-environment/linux-zen3/eccodes-2.47.3-5evbf6pb6kwkq44g7ynlmq6askpeosup/share/eccodes/definitions"
ICON_DEFS = (
    "/scratch/mch/cosuna/runml/eccodes-cosmo-mars/definitions:"
    "/user-environment/venvs/fdb/share/eccodes-cosmo-resources/definitions:"
    f"{STOCK_DEFS}"
)

import eccodes_context_switch as ecs
import eccodes

log("loaded eccodes_context_switch, using libeccodes:", ecs._LIB_PATH)

log("--- ensure_patched_eccodes() self-test ---")
try:
    ecs.ensure_patched_eccodes(STOCK_DEFS)
    log("PASSED: this eccodes build appears to have the fix")
    PATCHED = True
except RuntimeError as e:
    log("FAILED (as expected if unpatched):", str(e)[:200])
    PATCHED = False

if PATCHED:
    log()
    log("--- exercising definitions_path() context manager ---")

    path = "/scratch/mch/cosuna/varda-single/run/202610141800/outputs/interpolator/varda-single-global_202610141800_8.grib"
    with open(path, "rb") as f:
        while True:
            gid = eccodes.codes_grib_new_from_file(f)
            if gid is None:
                raise RuntimeError("QV not found")
            if eccodes.codes_get(gid, "shortName") == "QV":
                raw = eccodes.codes_get_message(gid)
                eccodes.codes_release(gid)
                break
            eccodes.codes_release(gid)

    import ctypes

    def decode(raw_bytes):
        buf = ctypes.create_string_buffer(raw_bytes, len(raw_bytes))
        ctx = ecs._lib.grib_context_get_default()
        h = ecs._lib.grib_handle_new_from_message(ctx, buf, len(raw_bytes))
        val = ctypes.c_long()
        ecs._lib.grib_get_long(h, b"paramId", ctypes.byref(val))
        ecs._lib.grib_handle_delete(h)
        return val.value

    with ecs.definitions_path(ICON_DEFS):
        log("inside ICON definitions_path():", decode(raw))

    with ecs.definitions_path(STOCK_DEFS):
        log("inside STOCK definitions_path():", decode(raw))

    with ecs.definitions_path(ICON_DEFS):
        log("inside ICON definitions_path() again:", decode(raw))

    with ecs.definitions_path(STOCK_DEFS):
        log("inside STOCK definitions_path() again:", decode(raw))

    log()
    log("=== module test completed successfully ===")
else:
    log()
    log("=== module correctly refused to proceed on unpatched eccodes ===")
