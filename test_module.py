import glob
import logging
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
RESULTS_FILE = REPO_ROOT / "module_test_results.log"
SAMPLE_GRIB = REPO_ROOT / "examples" / "sample_qv.grib2"
ICON_DEFINITIONS_DIR = Path(os.environ.get("ICON_DEFINITIONS_DIR", REPO_ROOT / "icon_definitions"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(RESULTS_FILE)],
)
log = logging.getLogger("test_module")

import eccodes_context_switch as ecs
import eccodes

log.info("loaded eccodes_context_switch, using libeccodes: %s", ecs._LIB_PATH)


def _find_stock_definitions() -> str:
    """Locate a real, on-disk eccodes definitions directory (containing
    boot.def). The custom-built libeccodes.so used here has no definitions
    baked in (ENABLE_MEMFS=OFF), so it needs an actual directory -- unlike
    the uenv-provided eccodes python package, whose bundled defaults may
    reference a virtual MEMFS path that only that package's own library
    knows how to resolve."""
    env_override = os.environ.get("ECCODES_STOCK_DEFINITIONS_DIR")
    if env_override:
        return env_override

    for candidate in eccodes.codes_definition_path().split(":"):
        if os.path.isfile(os.path.join(candidate, "boot.def")):
            return candidate

    for pattern in ("/user-environment/*/eccodes-*/share/eccodes/definitions",):
        for candidate in sorted(glob.glob(pattern)):
            if os.path.isfile(os.path.join(candidate, "boot.def")):
                return candidate

    raise RuntimeError(
        "could not locate a real eccodes definitions directory (with boot.def); "
        "set ECCODES_STOCK_DEFINITIONS_DIR explicitly"
    )


STOCK_DEFS = _find_stock_definitions()


def _find_icon_definitions() -> str:
    candidates = sorted(ICON_DEFINITIONS_DIR.glob("definitions.edzw-*"))
    if not candidates:
        raise RuntimeError(
            f"no ICON definitions found under {ICON_DEFINITIONS_DIR}; "
            "run `python3 install_icon_definitions.py` first (see README.md)"
        )
    return str(candidates[-1])


ICON_DEFS = f"{_find_icon_definitions()}:{STOCK_DEFS}"

log.info("--- ensure_patched_eccodes() self-test ---")
try:
    ecs.ensure_patched_eccodes(STOCK_DEFS)
    log.info("PASSED: this eccodes build appears to have the fix")
    PATCHED = True
except RuntimeError as e:
    log.info("FAILED (as expected if unpatched): %s", str(e)[:200])
    PATCHED = False

if PATCHED:
    log.info("--- exercising definitions_path() context manager ---")

    with open(SAMPLE_GRIB, "rb") as f:
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
        log.info("inside ICON definitions_path(): %s", decode(raw))

    with ecs.definitions_path(STOCK_DEFS):
        log.info("inside STOCK definitions_path(): %s", decode(raw))

    with ecs.definitions_path(ICON_DEFS):
        log.info("inside ICON definitions_path() again: %s", decode(raw))

    with ecs.definitions_path(STOCK_DEFS):
        log.info("inside STOCK definitions_path() again: %s", decode(raw))

    log.info("=== module test completed successfully ===")
else:
    log.info("=== module correctly refused to proceed on unpatched eccodes ===")
