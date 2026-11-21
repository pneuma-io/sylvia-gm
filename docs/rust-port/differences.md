# Agreed differences between the Python and Rust behaviour

Every place where the Rust port deliberately does not do what the Python reference does.
A difference is added here in the pull request that introduces it, after the manager agrees
it. Anything not listed here is expected to behave exactly as the Python snapshot does.

| # | Where | Python | Rust | Why | Test |
| --- | --- | --- | --- | --- | --- |
| 1 | `sylvia_base::pyjson::loads` | Accepts integers of any size | Refuses an integer literal outside the signed or unsigned 64-bit range | `serde_json` would read it as a float and write it back with an exponent, silently changing hashed bytes. No shipped content has such a value. | `pyjson::tests::wide_integers_are_refused_not_rounded` |
| 2 | `sylvia_base::pyjson::loads` | Accepts the literals `NaN`, `Infinity`, `-Infinity` | Refuses them | A JSON value in Rust cannot hold them, and the Python encoders that matter already run with `allow_nan=False`. | Covered by `serde_json`'s parser |
| 3 | Strings everywhere | A `str` can hold a lone UTF-16 surrogate (from `"\ud800"` in JSON) | A `String` cannot; such input is refused at parsing | Rust strings are valid Unicode. | `sylvia-domain` `identity_vectors::strict_json_reads_like_python` (a lone `\ud800` value and a lone `\udc00` key, through both strict readers) |
| 4 | `sylvia_base::pyjson::loads` and every reader built on it | Accepts JSON nested up to roughly a thousand arrays or objects; deeper raises `RecursionError`, which the owner reports as an internal error | Refuses JSON nested 128 or more levels deep with an ordinary rejection (127 levels read) | `serde_json`'s recursion limit, which counts down from 128 and fails on reaching zero. Python's limit is an accident of its interpreter stack, so there is no exact boundary to reproduce, and no shipped content comes near 127 levels. | `pyjson::tests::nesting_is_limited_to_127_levels`; `sylvia-domain` `identity_vectors::strict_json_reads_like_python` (200 levels) |

Open questions for the manager are raised in the pull request, not parked here.
