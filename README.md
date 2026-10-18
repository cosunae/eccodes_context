# Introduction

NWP ICON and ML Varda data is encoded with grib using the ICON definitions provided by DWD. 
https://opendata.dwd.de/weather/lib/grib/

The main issue is that these definitions provide a global rewrite of all the parameters provided by eccodes. 
For example, the eccodes definitions for 2m temperature, with param id 167, reads: 
```
#param id 167
#2 metre temperature
'2t' = { 
         discipline = 0 ; 
         parameterCategory = 0 ;
         parameterNumber = 0 ;
         typeOfFirstFixedSurface = 103 ;
         scaledValueOfFirstFixedSurface = 2 ;
         scaleFactorOfFirstFixedSurface = 0 ;
        }
```
is entirely rewritten by the ICON definitions as paramId 500011
```
#paramId: 500011
#2m Temperature
'500011' = {
         discipline = 0 ;
         parameterCategory = 0 ;
         parameterNumber = 0 ;
         typeOfFirstFixedSurface = 103 ;
         scaleFactorOfFirstFixedSurface = 0 ;
         scaledValueOfFirstFixedSurface = 2 ;
        }
```

Since they are both mapped to the same grib parameter (i.e. tripplet of discipline, parameterCategory, parameterNumber), the two definitions can not coexist. Otherwise the eccodes mapping from the grib triplet into a paramId,shortName would be ambiguous.

Working on a pure ICON data, this represents no problem, since the ICON definitions are complete to encode/decode all ICON parameters. 
However, for applications that want to process both IFS/ERA5 and ICON data in the same process, this is a fundamental issue, since the application needs to choose whether it is going to use canonical definitions or ICON definitions.

This provide a prototype to change the context so that we can load various definitions in a single process. 

The main idea is that within a high-level earthkit-data code, we could choose the definitions loaded within a context: 

```

import eccodes_context_switch as ecs

ecs.ensure_patched_eccodes(ICON_DEFS)  # once, at startup -- fails loudly if unpatched

with ecs.definitions_path(ICON_DEFS):
    decode_or_encode(lam_0_fields)       # earthkit operations for LAM fields

with ecs.definitions_path(DEFAULT_DEFS):
    decode_or_encode(global_fields)      # earthkit operations for global, IFS, ERA5 fields
```

# Bug fix

Use of `grib_context_reset()` for this use-case contains a bug. 
The prototype needs to run with a bugfix branch: 
https://github.com/cosunae/eccodes/tree/delete_context


# Build

```
cmake -S ../src -B build   -DCMAKE_BUILD_TYPE=Release   -DENABLE_FORTRAN=OFF   -DENABLE_NETCDF=OFF   -DENABLE_JPG=OFF   -DENABLE_PNG=OFF   -DENABLE_AEC=OFF   -DENABLE_TESTS=OFF   -DENABLE_EXTRA_TESTS=OFF   -DENABLE_BUILD_TOOLS=OFF   -DENABLE_EXAMPLES=OFF   -DENABLE_MEMFS=OFF   -DENABLE_INSTALL_ECCODES_DEFINITIONS=OFF   -DENABLE_INSTALL_ECCODES_SAMPLES=OFF 

cmake -C build
```


# Install the ICON definitions

`test_module.py` needs a local copy of DWD's ICON eccodes definitions to
exercise the context switch against. Fetch them with:

```
python3 install_icon_definitions.py
```

This downloads the latest `eccodes_definitions.edzw-*` archive listed at
https://opendata.dwd.de/weather/lib/grib/ and extracts it into
`./icon_definitions`. Useful options:

```
python3 install_icon_definitions.py --list              # show available versions
python3 install_icon_definitions.py --archive eccodes_definitions.edzw-2.47.0-2.tar.bz2
python3 install_icon_definitions.py --dest /some/other/dir
```

If the definitions were installed to a non-default location, point
`test_module.py` at it with `ICON_DEFINITIONS_DIR=/some/other/dir`.

# Run the test
```
/usr/bin/uenv run --view=rea-l-ch1 fdb/5.21:v2 -- bash -c '
export ECCODES_CONTEXT_SWITCH_LIB_PATH=<build_dir>/build/lib/libeccodes.so
python3 test_module.py
```

The test decodes `examples/sample_qv.grib2` (a single GRIB2 message checked
into this repo) once under the ICON definitions and once under the stock
eccodes definitions, confirming the two regimes produce different `paramId`s
and that switching back and forth repeatedly is safe. Results are logged to
both stdout and `module_test_results.log`.

See test_module.py for a low-level example. 
Full implementation will require a python API that wraps the low-level functions in python.

# Reset the context or multiple contexts

Once we reset the context, loading/decoding the next message is again expensive since the whole set of definitions need to be loaded in memory.
A different approach that allows two context to coexists and use in memory would solve this issue. 
See RESET_CONTEXT_OR_MULTIPLE_CONTEXT.md for details. 
The eccodes API does not exists for that use-case.