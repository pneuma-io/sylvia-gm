# Third-party code the port translates

Some of the port reproduces another project's algorithm or code closely enough to be a
translation of it, not only a reimplementation of its behaviour. Those translations carry the
original licence. This file lists each one; the release's dependency notices
(`dependency-notices.txt` and `licenses/`) must include each licence text before the Rust build
ships. Lane 5 adds them when packaging moves to the Rust build.

A lane that translates third-party code adds its row here in the same pull request, with the
upstream version it read and the Rust files that hold the translation. Behaviour reproduced
only from observation (vectors), without reading or following the source, does not need a row.

| Rust code | Translates | Upstream version | Licence | Added by |
| --- | --- | --- | --- | --- |
| `sylvia-base/src/pyjson.rs` | CPython's JSON encoder rules and float `repr` (`Lib/json/encoder.py`, `Python/dtoa.c` behaviour) | CPython 3.12.11 | PSF License Version 2 | manager |
| `sylvia-base/src/pystr/` | CPython `str` methods, `int()`/`float()` string parsing, `repr` (`Objects/unicodeobject.c`, `Objects/longobject.c`, `Objects/floatobject.c`, `Python/pystrtod.c`) | CPython 3.12.11 | PSF License Version 2 | lanes 1 and 2 |
| `sylvia-engine/src/pyrandom.rs` | CPython's `random.Random` (`Lib/random.py`, `Modules/_randommodule.c`), including MT19937 | CPython 3.12.11 | PSF License Version 2; MT19937 also BSD-3-Clause (Makoto Matsumoto and Takuji Nishimura) | lane 2 |
| `sylvia-domain/src/content/schema.rs` | `jsonschema`'s Draft 2020-12 validation order and messages | jsonschema 4.26 | MIT | lane 1 |
| `sylvia-domain` zip reading (lane 1's archive admission) | CPython's `zipfile` read path | CPython 3.12.11 | PSF License Version 2 | lane 1 |
| `sylvia-base` PyYAML port (lane 4) | PyYAML's reader, scanner, parser, composer, constructor, representer, serializer and emitter | PyYAML 6.0.3 | MIT | lane 4 |
| `sylvia-base` PyYAML port, `Loader::C` (lane 4) | libyaml's scanner and parser behaviour | libyaml 0.2.5 | MIT | lane 4 |
| `sylvia-provider` SSE decoding, request building and URL normalisation (lane 4) | The OpenAI Python SDK's SSE decoder and request assembly; `httpx.URL` normalisation | openai 2.53.0; httpx 0.28.1 | Apache-2.0 (openai); BSD-3-Clause (httpx) | lane 4 |
| `sylvia-store/src/launch.rs` (`py_fromhex`) | CPython's `bytes.fromhex` (`Objects/bytesobject.c`, `_PyBytes_FromHex`): whitespace skipped before each pair | CPython 3.12.11 | PSF License Version 2 | lane 3 |
| `sylvia-store/src/safe_file/linux.rs` (`normpath`, `abspath`) | CPython's `posixpath.normpath` and `posixpath.abspath` (`Lib/posixpath.py`) | CPython 3.12.11 | PSF License Version 2 | lane 3 |
| `sylvia-store/src/sessions.rs` (`is_reserved_windows_name`) | CPython's `PureWindowsPath.is_reserved` and `_WIN_RESERVED_NAMES` (`Lib/pathlib.py`) | CPython 3.12.11 | PSF License Version 2 | lane 3 |

Rows for code not yet merged are listed so that the licence is not forgotten when it lands;
the lane confirms the row and the version in its pull request.
