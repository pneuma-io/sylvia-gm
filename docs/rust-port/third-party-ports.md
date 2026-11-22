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
| `sylvia-base/src/pyjson.rs`, `pyjson/decode_error.rs` | CPython's JSON encoder rules and float `repr` (`Lib/json/encoder.py`, `Python/dtoa.c` behaviour), and its decoder: where it finds the first error, how `JSONDecodeError` words it, and where it calls `parse_constant` and `object_pairs_hook` (`Modules/_json.c` scanner, `Lib/json/decoder.py`, `Lib/json/__init__.py`) | CPython 3.12.11 | PSF License Version 2 | manager |
| `sylvia-base/src/pystr/` | CPython `str` methods, `int()`/`float()` string parsing, `repr` (`Objects/unicodeobject.c`, `Objects/longobject.c`, `Objects/floatobject.c`, `Python/pystrtod.c`) | CPython 3.12.11 | PSF License Version 2 | lanes 1 and 2 |
| `sylvia-engine/src/pyrandom.rs` | CPython's `random.Random` (`Lib/random.py`, `Modules/_randommodule.c`), including MT19937 | CPython 3.12.11 | PSF License Version 2; MT19937 also BSD-3-Clause (Makoto Matsumoto and Takuji Nishimura) | lane 2 |
| `sylvia-engine/src/pynum.rs` | CPython's builtin `sum()` (`builtin_sum_impl` in `Python/bltinmodule.c`: the integer and Neumaier-compensated float fast paths and the C `long` bounds) | CPython 3.12.11 | PSF License Version 2 | lane 2 |
| `sylvia-engine/src/expr/ops.rs`, `sylvia-engine/src/expr/compare.rs` | CPython's float `//`, `%` and `**` (`_float_div_mod`, `float_rem`, `float_pow` in `Objects/floatobject.c`) and `list.sort`'s `count_run` and `binarysort` (`Objects/listobject.c`) | CPython 3.12.11 | PSF License Version 2 | lane 2 |
| `sylvia-domain/src/content/schema.rs` | `jsonschema`'s Draft 2020-12 validation order and messages | jsonschema 4.26 | MIT | lane 1 |
| `sylvia-domain/src/package_archive/zipfile.rs` | CPython's `zipfile` read path (`Lib/zipfile/__init__.py`: end-of-central-directory search, central directory, extra fields, local headers, `ZipExtFile` reads and CRC checks, and their messages) | CPython 3.12.11 | PSF License Version 2 | lane 1 |
| `sylvia-domain/src/package_archive/zipfile_write.rs` | CPython's `zipfile` write path for seekable output (`ZipFile(..., 'w')`, `writestr`, `ZipInfo.FileHeader`, `_write_end_record`, `_get_compressor`'s DEFLATE parameters; and `StreamingZipWriter`: stored members streamed through `ZipFile.open(info, "w")` with the header rewrite on close, and `_write_end_record`'s zip64 central-directory extras and zip64 end records) | CPython 3.12.11 | PSF License Version 2 | lane 1; streaming writer lane 3 (#376) |
| `libz-sys` (dependency, `static`: builds madler zlib from source; used through flate2's `zlib` backend) | zlib's DEFLATE and INFLATE, compiled as C, not translated | zlib 1.3.1 (libz-sys 1.1.24) | zlib License | lane 1 |
| `sylvia-base/src/pyyaml/` (`scanner.rs`, `parser.rs`, `compose.rs`, `construct.rs`, `emit.rs`, `error.rs`, `value.rs`) | PyYAML's reader, scanner, parser, composer, resolver, `SafeConstructor`, `SafeRepresenter`, serializer and emitter (`lib/yaml/*.py`), and the `CParser` composer and event conversion of `yaml/_yaml.pyx` | PyYAML 6.0.3 | MIT | lane 4 |
| `sylvia-base/src/pybase64.rs` | CPython's `binascii.a2b_base64` and `binascii.b2a_base64` as `base64.decodebytes` and `base64.encodebytes` call them (`Modules/binascii.c`) | CPython 3.12.11 | PSF License Version 2 | lane 4 |
| `sylvia-base/src/pyyaml/scanner_c.rs`, `parser_c.rs` (`Loader::C`) | libyaml's reader, scanner and parser (`src/reader.c`, `src/scanner.c`, `src/parser.c`) | libyaml 0.2.5 | MIT | lane 4 |
| `sylvia-provider/src/sse.rs`, `openai_compat.rs`, `pyobj.rs`, `chat.rs` | The OpenAI Python SDK's SSE decoder (`_streaming.py`), request assembly (`_base_client.py`, `_utils/_json.py`) and `construct` model reading (`_models.py`) | openai 2.53.0 | Apache-2.0 | lane 4 |
| `sylvia-provider/src/httpx_url.rs` | `httpx.URL` parsing and normalisation (`httpx/_urlparse.py`, `httpx/_urls.py`) | httpx 0.28.1 | BSD-3-Clause | lane 4 |
| `sylvia-provider/src/proxy.rs` | `urllib.request.getproxies_environment` and the registry parsing of `getproxies_registry` (`Lib/urllib/request.py`), and `Reg2Py` with `countStrings`/`fixupMultiSZ` for the value types `sylvia_base::winreg` leaves to its callers (`PC/winreg.c`); httpx's `get_environment_proxies`, `URLPattern` and its priority order (`httpx/_utils.py`) | CPython 3.12.11; httpx 0.28.1 | PSF License Version 2; BSD-3-Clause | lane 4 |
| `sylvia-provider/src/net.rs` | httpx's response header decoding (`httpx/_models.py`, `Headers`) and its client timing defaults | httpx 0.28.1 | BSD-3-Clause | lane 4 |
| `sylvia-base/src/pyurl.rs` | CPython's `urllib.parse`: `urlsplit` with `_splitnetloc`, `_check_bracketed_netloc`, `_check_bracketed_host` and `_checknetloc`, `urlunsplit`, the `hostname`, `port`, `username` and `password` properties, `unquote` and `_unquote_impl` (`Lib/urllib/parse.py`); `ipaddress`'s address parsing (`Lib/ipaddress.py`). Lane 5's port (`desktop_update/url.rs`, PR 289) merged with lane 4's | CPython 3.12.11 | PSF License Version 2 | lanes 4 and 5 |
| `sylvia-provider/src/config/host_browser/mod.rs` (`posix_normpath`) | CPython's `posixpath.normpath` (`Lib/posixpath.py`, `_path_normpath` in `Python/fileutils.c`) | CPython 3.12.11 | PSF License Version 2 | lane 4 |
| `sylvia-store/src/launch.rs` (`py_fromhex`) | CPython's `bytes.fromhex` (`Objects/bytesobject.c`, `_PyBytes_FromHex`): whitespace skipped before each pair | CPython 3.12.11 | PSF License Version 2 | lane 3 |
| `sylvia-store/src/safe_file/linux.rs` (`normpath`, `abspath`) | CPython's `posixpath.normpath` and `posixpath.abspath` (`Lib/posixpath.py`) | CPython 3.12.11 | PSF License Version 2 | lane 3 |
| `sylvia-store/src/sessions.rs` (`is_reserved_windows_name`) | CPython's `PureWindowsPath.is_reserved` and `_WIN_RESERVED_NAMES` (`Lib/pathlib.py`) | CPython 3.12.11 | PSF License Version 2 | lane 3 |
| `sylvia-owner/src/desktop_update/manifest.rs` (`b64decode_strict`) | CPython's `binascii.a2b_base64` in strict mode, as `base64.b64decode(..., validate=True)` calls it (`Modules/binascii.c`) | CPython 3.12.11 | PSF License Version 2 | lane 5 |
| `sylvia-owner/src/desktop_update/windows.rs` (`disk_free`, `dirname_wide`) | CPython's `os__getdiskusage_impl` and `_dirnameW` (`Modules/posixmodule.c`), as `shutil.disk_usage` calls them | CPython 3.12.11 | PSF License Version 2 | lane 5 |
| `sylvia-base/src/winreg.rs` (`sz_units`, `dword`, `Key::query_raw`) | CPython's `winreg.QueryValueEx` and `Reg2Py` for `REG_SZ` (`PC/winreg.c`): the size query, the doubling read and the cut at the first NUL | CPython 3.12 (source read from the 3.12 branch; vectors from 3.12.11) | PSF License Version 2 | lane 5 |
| `sylvia-owner/src/desktop_update/installed.rs` (`list2cmdline`) | CPython's `subprocess.list2cmdline` (`Lib/subprocess.py`) | CPython 3.12.11 | PSF License Version 2 | lane 5 |
| `sylvia-owner/src/webui/file_response.rs` (`prepare`, `etags`, `etag_match`, `http_range`) | aiohttp's `FileResponse.prepare`, `_make_response`, `_prepare_open_file` and `_etag_match` (`web_fileresponse.py`), the request's `if_match`, `_etag_values` and `http_range` (`web_request.py`) and `LIST_QUOTED_ETAG_RE` (`helpers.py`) | aiohttp 3.14.3 | Apache License 2.0 | lane 5 |
| `sylvia-owner/src/webui/file_response.rs` (`parsedate`) | CPython's `email._parseaddr._parsedate_tz` (`Lib/email/_parseaddr.py`), as `email.utils.parsedate` and aiohttp's `parse_http_date` call it | CPython 3.12.11 | PSF License Version 2 | lane 5 |

| `sylvia-owner/src/dictation/features.rs` (`RealFft`, `SinCos`, `radf4`, `radf5`) | pocketfft's real transform: `rfftp` (`factorize`, `comp_twiddle`, `exec`, `radf4`, `radf5`) and `sincos_2pibyn` (`pocketfft_hdronly.h`), and NumPy's half-complex unpacking (`rfft_impl` in `numpy/fft/_pocketfft_umath.cpp`) | pocketfft C++ at NumPy 2.5.4's submodule `33ae5dc`; NumPy 2.5.4 | BSD-3-Clause (pocketfft: Max-Planck-Society; NumPy) | lane 5 |
| `sylvia-owner/src/dictation/features.rs` (`hann_window`, `mel_filters`, `linspace`, `pad_reflect`, `cabs`) | NumPy's `hanning`, `linspace`, `pad(mode="reflect")` (`_set_reflect_both`) and the SIMD complex `absolute` (`loops_unary_complex.dispatch.c.src`) | NumPy 2.5.4 | BSD-3-Clause | lane 5 |
| `sylvia-owner/src/dictation/features.rs` (`log_mel`), `sylvia-owner/src/dictation/audio.rs` | faster-whisper's `FeatureExtractor` (`feature_extractor.py`) and `decode_audio`, `_group_frames` (`audio.py`) | faster-whisper 1.2.1 | MIT | lane 5 |
| `sylvia-owner/src/dictation/whisper.rs`, `sylvia-owner/src/dictation/vad.rs` | faster-whisper's `WhisperModel.transcribe`, `generate_segments`, `generate_with_fallback`, `_split_segments_by_timestamps`, `detect_language`, `restore_speech_timestamps` and `SpeechTimestampsMap` (`transcribe.py`), `Tokenizer`, `non_speech_tokens` and `get_suppressed_tokens` (`tokenizer.py`), `SileroVADModel`, `get_speech_timestamps` and `collect_chunks` (`vad.py`), for the options the worker sets | faster-whisper 1.2.1 | MIT | lane 5 |

Rows for code not yet merged are listed so that the licence is not forgotten when it lands;
the lane confirms the row and the version in its pull request.

Not a translation, but a notice the release needs: the TLS trust roots of the provider HTTP client are the `webpki-roots` crate (1.0, CDLA-Permissive-2.0), Mozilla's CA bundle, as httpx uses `certifi`'s copy of the same bundle. `SSL_CERT_FILE` and `SSL_CERT_DIR` replace it, as they do for httpx.

## Native libraries the Rust build loads

Not translations: the Rust owner loads these libraries, the same files the Python build ships,
so its results are theirs. Packaging must ship each one beside `sylvia-gm-owner` with its
licence notice, and the hash pins the file the vectors were recorded with.

| Library | Loaded by | File and SHA-256 | Version | Licence | Added by |
| --- | --- | --- | --- | --- | --- |
| OpenBLAS (NumPy's scipy-openblas, ILP64) | `sylvia-owner/src/dictation/openblas/windows.rs` | `libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll`, `ed4f167a5330424524f45258e7ca2c8d7f03c0cba1f30ee4985553ac1f88ecf8` (from the NumPy 2.5.4 wheel in `uv.lock`) | OpenBLAS 0.3.34.106.0 | BSD-3-Clause | lane 5 |
| libswresample, libavutil (FFmpeg) | `sylvia-owner/src/dictation/swresample/windows.rs` | `swresample-6.dll`, `3d950dfb94a55fc3b25fdd55ea4c49aa0361506d0c304d2615d3fa49a21634ad`; `avutil-60.dll`, `4afe3a74c43b9860d64ebdbdaef30b8758d907a1d3128ff8fc0e28d3b7227008` (the project's MSVC LGPL build, `licenses/dictation/PYAV-LGPL-BUILD.json`) | FFmpeg 8.1.2 | LGPL-2.1-or-later (source offer as for the PyAV build) | lane 5 |
| CTranslate2 (with Intel oneMKL and oneDNN 3.1.1 linked statically inside it) | `sylvia-owner/src/dictation/engine.rs` (ct2rs `=0.10.2`'s C++ bridge, linked against an import library generated by `docs/rust-port/tools/ct2_import_lib.sh`, delay-loaded) | `ctranslate2.dll`, `ca1127837d032bf8217d0cbcd68e8cef1f93bda3111eff00ced98795d9e51160` (from the ctranslate2 4.8.3 wheel in `uv.lock`) | CTranslate2 4.8.3 | MIT (`licenses/dictation/CTRANSLATE2-MIT.txt`); oneMKL under the Intel Simplified Software License; oneDNN Apache-2.0 | lane 5 |
| Intel OpenMP runtime | loaded by `ctranslate2.dll` | `libiomp5md.dll`, `982233366b0afcda1e0f55a0b134097e35b779613f54ddb69e685e6cd06b755f` (the same wheel) | build 20250910 | Intel Simplified Software License | lane 5 |
| ONNX Runtime | `sylvia-owner/src/dictation/vad.rs` (ort `=2.0.0-rc.15`, `load-dynamic`) | `onnxruntime.dll`, `aa7737cc5c987286f09f6f12304fa786ea697b11b4ca7ab59a027ff4ce343a9c`; `onnxruntime_providers_shared.dll`, `029af27204cff24103c5837a0eb507e4a31ef269116f400c6b25c5457ddc9a32` (from the onnxruntime 1.31.0 wheel in `uv.lock`) | ONNX Runtime 1.31.0 | MIT (with the wheel's third-party notices) | lane 5 |
| Silero VAD v6 (an ONNX model, data) | `sylvia-owner/src/dictation/vad.rs` | `silero_vad_v6.onnx`, `4cbf549b8326f60f80f2536d9eefeb450a9abe83365a098031c89719f1be17d2` (faster-whisper 1.2.1's `assets/`) | Silero VAD v6 | MIT (Silero Team, in `licenses/dictation/FASTER-WHISPER-MIT.txt`) | lane 5 |

Packaging: the Rust worker checks every file above against its hash before it loads any of them
(`dictation::speech::verify`), so packaging copies them unchanged from the locked wheels into the
owner's folder, flat. `ctranslate2.dll` is delay-loaded: an owner without it still starts, and
its dictation answers as a build without the speech runtime.
