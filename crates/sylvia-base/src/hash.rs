//! SHA-256 helpers matching Python's `hashlib.sha256(...).hexdigest()`.

use sha2::{Digest, Sha256};

/// Lowercase hex SHA-256 of `bytes`.
pub fn sha256_hex(bytes: impl AsRef<[u8]>) -> String {
    hex::encode(Sha256::digest(bytes.as_ref()))
}

/// Raw SHA-256 of `bytes`.
pub fn sha256(bytes: impl AsRef<[u8]>) -> [u8; 32] {
    Sha256::digest(bytes.as_ref()).into()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_python_hexdigest() {
        // hashlib.sha256(b"").hexdigest()
        assert_eq!(sha256_hex(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        // hashlib.sha256(b"abc").hexdigest(); the non-ASCII case is in tests/pyjson_vectors.rs.
        assert_eq!(sha256_hex(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        assert_eq!(hex::encode(sha256(b"abc")), sha256_hex(b"abc"));
    }
}
