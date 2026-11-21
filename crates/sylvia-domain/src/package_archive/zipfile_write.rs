//! CPython 3.12.11 `zipfile`'s write path, as `ZipFile(seekable_stream, "w")` with
//! `writestr(ZipInfo, bytes)` uses it: the bytes Sylvia's package archives are hashed by.
//!
//! The port covers what materialization writes: regular members, `ZIP_STORED` or
//! `ZIP_DEFLATED`, to a seekable stream, so no data descriptor and no ZIP64 (package archives
//! are bounded far below `ZIP64_LIMIT`). DEFLATE is zlib's, through flate2's zlib backend, with
//! the parameters `zipfile._get_compressor` gives `zlib.compressobj`: level
//! `Z_DEFAULT_COMPRESSION` (6), `DEFLATED`, `wbits=-15`, memLevel 8 and the default strategy.

use super::zipfile::{ZIP_DEFLATED, ZIP_STORED};
use crate::error::{DomainError, DomainResult};
use flate2::{Compress, Compression, Crc, FlushCompress, Status};

/// `ZIP64_LIMIT`.
const ZIP64_LIMIT: u64 = (1 << 31) - 1;
/// `ZIP_FILECOUNT_LIMIT`.
const ZIP_FILECOUNT_LIMIT: usize = (1 << 16) - 1;
/// `DEFAULT_VERSION`.
const DEFAULT_VERSION: u8 = 20;
/// `_MASK_UTF_FILENAME`.
const UTF_FILENAME: u16 = 0x800;

/// The `ZipInfo` fields a write sets.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ZipMember {
    pub filename: String,
    /// `date_time`: year, month, day, hour, minute, second.
    pub date_time: (u16, u16, u16, u16, u16, u16),
    pub compress_type: u16,
    pub create_system: u8,
    pub external_attr: u32,
}

struct Written {
    member: ZipMember,
    crc: u32,
    compress_size: u64,
    file_size: u64,
    header_offset: u64,
}

/// `ZipFile(stream, "w")`: members in order, then the central directory on [`ZipWriter::finish`].
#[derive(Default)]
pub struct ZipWriter {
    out: Vec<u8>,
    written: Vec<Written>,
}

fn dos(date_time: (u16, u16, u16, u16, u16, u16)) -> (u16, u16) {
    let (year, month, day, hour, minute, second) = date_time;
    let dosdate = (year.wrapping_sub(1980) << 9) | (month << 5) | day;
    let dostime = (hour << 11) | (minute << 5) | (second / 2);
    (dosdate, dostime)
}

/// `_encodeFilenameFlags()`: ASCII names as they are, others as UTF-8 with the UTF-8 flag.
fn encode_filename(filename: &str, flag_bits: u16) -> (&[u8], u16) {
    if filename.is_ascii() {
        (filename.as_bytes(), flag_bits)
    } else {
        (filename.as_bytes(), flag_bits | UTF_FILENAME)
    }
}

/// `zlib.compressobj(-1, DEFLATED, -15)`: `compress(data)` then `flush()`.
fn deflate(data: &[u8]) -> Vec<u8> {
    let mut compressor = Compress::new(Compression::new(6), false);
    let mut out = Vec::with_capacity(data.len() / 2 + 64);
    let mut fed = 0usize;
    while fed < data.len() {
        out.reserve(64 * 1024);
        let before = compressor.total_in();
        compressor.compress_vec(&data[fed..], &mut out, FlushCompress::None).expect("deflate accepts input");
        fed += (compressor.total_in() - before) as usize;
    }
    loop {
        out.reserve(64 * 1024);
        match compressor.compress_vec(&[], &mut out, FlushCompress::Finish).expect("deflate finishes") {
            Status::StreamEnd => break,
            Status::Ok | Status::BufError => {}
        }
    }
    out
}

impl ZipWriter {
    /// A new, empty archive.
    pub fn new() -> Self {
        Self::default()
    }

    /// `FileHeader(zip64=False)` for a member with known CRC and sizes.
    fn file_header(member: &ZipMember, crc: u32, compress_size: u64, file_size: u64) -> Vec<u8> {
        let (dosdate, dostime) = dos(member.date_time);
        let (filename, flag_bits) = encode_filename(&member.filename, 0);
        let mut header = Vec::with_capacity(30 + filename.len());
        header.extend_from_slice(b"PK\x03\x04");
        header.push(DEFAULT_VERSION); // extract_version
        header.push(0); // reserved
        header.extend_from_slice(&flag_bits.to_le_bytes());
        header.extend_from_slice(&member.compress_type.to_le_bytes());
        header.extend_from_slice(&dostime.to_le_bytes());
        header.extend_from_slice(&dosdate.to_le_bytes());
        header.extend_from_slice(&crc.to_le_bytes());
        header.extend_from_slice(&(compress_size as u32).to_le_bytes());
        header.extend_from_slice(&(file_size as u32).to_le_bytes());
        header.extend_from_slice(&(filename.len() as u16).to_le_bytes());
        header.extend_from_slice(&0u16.to_le_bytes()); // extra length
        header.extend_from_slice(filename);
        header
    }

    /// `writestr(zinfo, data)`.
    pub fn writestr(&mut self, member: ZipMember, data: &[u8]) -> DomainResult<()> {
        if member.compress_type != ZIP_STORED && member.compress_type != ZIP_DEFLATED {
            return Err(DomainError::type_error("That compression method is not supported"));
        }
        let file_size = data.len() as u64;
        // `zip64 = file_size * 1.05 > ZIP64_LIMIT`; package archives never reach it.
        if file_size as f64 * 1.05 > ZIP64_LIMIT as f64 || self.written.len() >= ZIP_FILECOUNT_LIMIT {
            return Err(DomainError::value("ZIP64 members are outside the port's write path"));
        }
        let header_offset = self.out.len() as u64;
        let mut crc = Crc::new();
        crc.update(data);
        let compressed = if member.compress_type == ZIP_DEFLATED { deflate(data) } else { data.to_vec() };
        let compress_size = compressed.len() as u64;
        if compress_size > ZIP64_LIMIT {
            return Err(DomainError::uncaught(
                "RuntimeError",
                "Compressed size too large, try using force_zip64",
            ));
        }
        // Python writes a provisional header, the data, then seeks back and rewrites the header
        // with the real CRC and sizes: the same bytes as writing the final header first.
        let mut member = member;
        if member.external_attr == 0 {
            member.external_attr = 0o600 << 16;
        }
        self.out.extend_from_slice(&Self::file_header(&member, crc.sum(), compress_size, file_size));
        self.out.extend_from_slice(&compressed);
        self.written.push(Written { member, crc: crc.sum(), compress_size, file_size, header_offset });
        Ok(())
    }

    /// `close()`: `_write_end_record()`, then the archive's bytes.
    pub fn finish(mut self) -> Vec<u8> {
        let start_dir = self.out.len() as u64;
        for written in &self.written {
            let member = &written.member;
            let (dosdate, dostime) = dos(member.date_time);
            let (filename, flag_bits) = encode_filename(&member.filename, 0);
            let entry = &mut self.out;
            entry.extend_from_slice(b"PK\x01\x02");
            entry.push(DEFAULT_VERSION); // create_version
            entry.push(member.create_system);
            entry.push(DEFAULT_VERSION); // extract_version
            entry.push(0); // reserved
            entry.extend_from_slice(&flag_bits.to_le_bytes());
            entry.extend_from_slice(&member.compress_type.to_le_bytes());
            entry.extend_from_slice(&dostime.to_le_bytes());
            entry.extend_from_slice(&dosdate.to_le_bytes());
            entry.extend_from_slice(&written.crc.to_le_bytes());
            entry.extend_from_slice(&(written.compress_size as u32).to_le_bytes());
            entry.extend_from_slice(&(written.file_size as u32).to_le_bytes());
            entry.extend_from_slice(&(filename.len() as u16).to_le_bytes());
            entry.extend_from_slice(&0u16.to_le_bytes()); // extra length
            entry.extend_from_slice(&0u16.to_le_bytes()); // comment length
            entry.extend_from_slice(&0u16.to_le_bytes()); // disk number
            entry.extend_from_slice(&0u16.to_le_bytes()); // internal_attr
            entry.extend_from_slice(&member.external_attr.to_le_bytes());
            entry.extend_from_slice(&(written.header_offset as u32).to_le_bytes());
            entry.extend_from_slice(filename);
        }
        let central_size = self.out.len() as u64 - start_dir;
        let count = self.written.len() as u16;
        self.out.extend_from_slice(b"PK\x05\x06");
        self.out.extend_from_slice(&0u16.to_le_bytes());
        self.out.extend_from_slice(&0u16.to_le_bytes());
        self.out.extend_from_slice(&count.to_le_bytes());
        self.out.extend_from_slice(&count.to_le_bytes());
        self.out.extend_from_slice(&(central_size as u32).to_le_bytes());
        self.out.extend_from_slice(&(start_dir as u32).to_le_bytes());
        self.out.extend_from_slice(&0u16.to_le_bytes()); // comment length
        self.out
    }
}
