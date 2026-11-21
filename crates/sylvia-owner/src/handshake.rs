//! Launch handshake, the owner side of `infrastructure/launch_handshake.py`.
//!
//! The shell sends a 256-bit secret in the launch envelope. The owner answers with
//! HMAC-SHA256(secret, transcript label) in hex and never repeats the secret.

use hmac::{Hmac, Mac};
use sha2::Sha256;

const HANDSHAKE_TRANSCRIPT: &[u8] = b"sylvia-gm-f6-local-owner-handshake-v1";

/// A handshake failure. Python raises `LaunchHandshakeError`, which ends the owner at startup.
#[derive(Debug, PartialEq, Eq)]
pub struct HandshakeError(pub &'static str);

/// `derive_owner_response(secret_hex)`.
pub fn derive_owner_response(secret_hex: &str) -> Result<String, HandshakeError> {
    // Python checks `len(secret_hex) != 64` in characters before decoding.
    if secret_hex.chars().count() != 64 {
        return Err(HandshakeError("Owner secret must be 64 hex characters"));
    }
    let secret =
        hex::decode(secret_hex).map_err(|_| HandshakeError("Owner secret is not valid hexadecimal"))?;
    let mut mac = Hmac::<Sha256>::new_from_slice(&secret).expect("HMAC accepts any key length");
    mac.update(HANDSHAKE_TRANSCRIPT);
    Ok(hex::encode(mac.finalize().into_bytes()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_python_reference() {
        // derive_owner_response("00" * 32) and ("0123456789abcdef" * 4) from the snapshot.
        assert_eq!(
            derive_owner_response(&"00".repeat(32)).unwrap(),
            include_str!("../tests/vectors/handshake_zero.txt").trim()
        );
        assert_eq!(
            derive_owner_response(&"0123456789abcdef".repeat(4)).unwrap(),
            include_str!("../tests/vectors/handshake_pattern.txt").trim()
        );
    }

    #[test]
    fn rejects_what_python_rejects() {
        assert_eq!(
            derive_owner_response("abc"),
            Err(HandshakeError("Owner secret must be 64 hex characters"))
        );
        assert_eq!(
            derive_owner_response(&"zz".repeat(32)),
            Err(HandshakeError("Owner secret is not valid hexadecimal"))
        );
    }
}
