//! Take the authority owner lock for a database path, for `tests/vectors/lock_interop.py`.
//!
//! `owner_lock_probe <database> try` prints `acquired` or `conflict` and exits.
//! `owner_lock_probe <database> hold` prints `acquired`, then holds the lock until stdin closes.

use std::io::{BufRead, Write};
use std::path::Path;
use sylvia_store::owner_lock::{authority_lock_identity, OwnerLock, OwnerLockError};

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    let (Some(database), Some(mode)) = (arguments.get(1), arguments.get(2)) else {
        eprintln!("usage: owner_lock_probe <database> try|hold");
        std::process::exit(2);
    };
    let identity = authority_lock_identity(Path::new(database));
    match OwnerLock::acquire(&identity) {
        Ok(lock) => {
            println!("acquired");
            std::io::stdout().flush().ok();
            if mode == "hold" {
                for _ in std::io::stdin().lock().lines() {}
            }
            drop(lock);
        }
        Err(OwnerLockError::Conflict(error)) => println!("conflict: {}", error.message),
        Err(OwnerLockError::Io(error)) => {
            println!("io: {error}");
            std::process::exit(1);
        }
    }
}
