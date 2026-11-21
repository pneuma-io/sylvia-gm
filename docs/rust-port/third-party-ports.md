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
| `sylvia-base/src/pyjson.rs`, `pyjson/decode_error.rs` | CPython's JSON encoder rules and float `repr` (`Lib/json/encoder.py`, `Python/dtoa.c` behaviour), and where its decoder finds the first error and how `JSONDecodeError` words it (`Modules/_json.c` scanner, `Lib/json/decoder.py`, `Lib/json/__init__.py`) | CPython 3.12.11 | PSF License Version 2 | manager |
| `sylvia-base/src/pystr/` | CPython `str` methods, `int()`/`float()` string parsing, `repr` (`Objects/unicodeobject.c`, `Objects/longobject.c`, `Objects/floatobject.c`, `Python/pystrtod.c`) | CPython 3.12.11 | PSF License Version 2 | lanes 1 and 2 |
| `sylvia-engine/src/pyrandom.rs` | CPython's `random.Random` (`Lib/random.py`, `Modules/_randommodule.c`), including MT19937 | CPython 3.12.11 | PSF License Version 2; MT19937 also BSD-3-Clause (Makoto Matsumoto and Takuji Nishimura) | lane 2 |
| `sylvia-domain/src/content/schema.rs` | `jsonschema`'s Draft 2020-12 validation order and messages | jsonschema 4.26 | MIT | lane 1 |
| `sylvia-domain/src/package_archive/zipfile.rs` | CPython's `zipfile` read path (`Lib/zipfile/__init__.py`: end-of-central-directory search, central directory, extra fields, local headers, `ZipExtFile` reads and CRC checks, and their messages) | CPython 3.12.11 | PSF License Version 2 | lane 1 |
| `sylvia-base/src/pyyaml/` (`scanner.rs`, `parser.rs`, `compose.rs`, `construct.rs`, `emit.rs`, `error.rs`, `value.rs`) | PyYAML's reader, scanner, parser, composer, resolver, `SafeConstructor`, `SafeRepresenter`, serializer and emitter (`lib/yaml/*.py`), and the `CParser` composer and event conversion of `yaml/_yaml.pyx` | PyYAML 6.0.3 | MIT | lane 4 |
| `sylvia-base/src/pybase64.rs` | CPython's `binascii.a2b_base64` and `binascii.b2a_base64` as `base64.decodebytes` and `base64.encodebytes` call them (`Modules/binascii.c`) | CPython 3.12.11 | PSF License Version 2 | lane 4 |
| `sylvia-base/src/pyyaml/scanner_c.rs`, `parser_c.rs` (`Loader::C`) | libyaml's reader, scanner and parser (`src/reader.c`, `src/scanner.c`, `src/parser.c`) | libyaml 0.2.5 | MIT | lane 4 |
| `sylvia-provider/src/sse.rs`, `openai_compat.rs`, `pyobj.rs`, `chat.rs` | The OpenAI Python SDK's SSE decoder (`_streaming.py`), request assembly (`_base_client.py`, `_utils/_json.py`) and `construct` model reading (`_models.py`) | openai 2.53.0 | Apache-2.0 | lane 4 |
| `sylvia-provider/src/httpx_url.rs` | `httpx.URL` parsing and normalisation (`httpx/_urlparse.py`, `httpx/_urls.py`) | httpx 0.28.1 | BSD-3-Clause | lane 4 |
| `sylvia-base/src/pyurl.rs` | CPython's `urllib.parse`: `urlsplit` with `_splitnetloc`, `_check_bracketed_netloc`, `_check_bracketed_host` and `_checknetloc`, `urlunsplit`, the `hostname`, `port`, `username` and `password` properties, `unquote` and `_unquote_impl` (`Lib/urllib/parse.py`); `ipaddress`'s address parsing (`Lib/ipaddress.py`). Lane 5's port (`desktop_update/url.rs`, PR 289) merged with lane 4's | CPython 3.12.11 | PSF License Version 2 | lanes 4 and 5 |
| `sylvia-provider/src/config/host_browser/mod.rs` (`posix_normpath`) | CPython's `posixpath.normpath` (`Lib/posixpath.py`, `_path_normpath` in `Python/fileutils.c`) | CPython 3.12.11 | PSF License Version 2 | lane 4 |
| `sylvia-store/src/launch.rs` (`py_fromhex`) | CPython's `bytes.fromhex` (`Objects/bytesobject.c`, `_PyBytes_FromHex`): whitespace skipped before each pair | CPython 3.12.11 | PSF License Version 2 | lane 3 |
| `sylvia-store/src/safe_file/linux.rs` (`normpath`, `abspath`) | CPython's `posixpath.normpath` and `posixpath.abspath` (`Lib/posixpath.py`) | CPython 3.12.11 | PSF License Version 2 | lane 3 |
| `sylvia-store/src/sessions.rs` (`is_reserved_windows_name`) | CPython's `PureWindowsPath.is_reserved` and `_WIN_RESERVED_NAMES` (`Lib/pathlib.py`) | CPython 3.12.11 | PSF License Version 2 | lane 3 |
| `sylvia-owner/src/desktop_update/manifest.rs` (`b64decode_strict`) | CPython's `binascii.a2b_base64` in strict mode, as `base64.b64decode(..., validate=True)` calls it (`Modules/binascii.c`) | CPython 3.12.11 | PSF License Version 2 | lane 5 |
| `sylvia-owner/src/desktop_update/windows.rs` (`disk_free`, `dirname_wide`) | CPython's `os__getdiskusage_impl` and `_dirnameW` (`Modules/posixmodule.c`), as `shutil.disk_usage` calls them | CPython 3.12.11 | PSF License Version 2 | lane 5 |

Rows for code not yet merged are listed so that the licence is not forgotten when it lands;
the lane confirms the row and the version in its pull request.
