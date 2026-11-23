"""Python's exact bytes for the audit export document and the record view's size, for the Rust
unit test. The writer below is copied from AuthorityOwner.export_admin_full_audit; json.dumps is
the part under test."""

import json

traces = [
    {"exchange_id": "ex-1", "request": {"messages": [{"role": "user", "content": "Héllo — ✓"}], "n": 1.5}, "empty": {}, "list": []},
    {"nested": [[1, 2], {"z": None, "a": True}], "text": "line\nbreak\t\"quoted\""},
]
diagnostics = [{"code": "rejected", "detail": "ünïcode"}]


def document(enabled, traces, diagnostics):
    out = bytearray()
    out += b'{\n  "enabled": '
    out += b"true" if enabled else b"false"
    out += f',\n  "trace_count": {len(traces)},\n  "traces": ['.encode()
    for index, trace in enumerate(traces):
        out += b"\n" if index == 0 else b",\n"
        encoded = json.dumps(trace, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        out += b"    " + encoded.replace(b"\n", b"\n    ")
    if traces:
        out += b"\n  "
    out += b'],\n  "safe_mechanics_diagnostics": ['
    for index, diagnostic in enumerate(diagnostics):
        out += b"\n" if index == 0 else b",\n"
        encoded = json.dumps(diagnostic, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
        out += b"    " + encoded.replace(b"\n", b"\n    ")
    if diagnostics:
        out += b"\n  "
    out += b"]\n}\n"
    return bytes(out)


print(json.dumps({
    "traces": traces,
    "diagnostics": diagnostics,
    "document": document(True, traces, diagnostics).decode("utf-8"),
    "empty_document": document(False, [], []).decode("utf-8"),
    "record_bytes": [
        len(json.dumps({"index": i, "trace": t}, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode())
        for i, t in enumerate(traces)
    ],
}, indent=1))
