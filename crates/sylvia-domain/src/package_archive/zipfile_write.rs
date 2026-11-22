//! CPython 3.12.11 `zipfile`'s write path, as `ZipFile(seekable_stream, "w")` with
//! `writestr(ZipInfo, bytes)` uses it: the bytes Sylvia's package archives are hashed by.
//!
//! [`ZipWriter`] covers what materialization writes: regular members, `ZIP_STORED` or
//! `ZIP_DEFLATED`, to a seekable stream, so no data descriptor and no ZIP64 (package archives
//! are bounded far below `ZIP64_LIMIT`). [`StreamingZipWriter`] covers what the portable campaign
//! export writes: stored members streamed with `open(info, "w")` to a seekable file, with the
//! zip64 extras and end records of archives past `ZIP64_LIMIT` or `ZIP_FILECOUNT_LIMIT`. DEFLATE is zlib's, through flate2's zlib backend, with
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

/// `ZIP64_VERSION`.
const ZIP64_VERSION: u8 = 45;

/// A failure of the streaming writer: the stream's own error, or the `RuntimeError` CPython's
/// `_ZipWriteFile.close` raises when a member outgrows the size its header was opened for.
#[derive(Debug)]
pub enum ZipWriteError {
    /// `OSError` from the stream.
    Io(std::io::Error),
    /// `RuntimeError(message)`.
    Runtime(&'static str),
}

impl From<std::io::Error> for ZipWriteError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

/// A stored member written through [`StreamingZipWriter`].
struct StreamedMember {
    member: ZipMember,
    crc: u32,
    size: u64,
    header_offset: u64,
    /// The member was opened as zip64, so `FileHeader` raised its versions to `ZIP64_VERSION`.
    zip64: bool,
}

/// `ZipFile(seekable_file, "w", compression=ZIP_STORED, allowZip64=True)` streaming stored
/// members with `open(info, "w")`, as the portable campaign export writes its archive: each
/// member's header is written with the size it was opened for, then its data, then the header
/// again with the real CRC and sizes (`_ZipWriteFile.close` on a seekable stream, so never a
/// data descriptor); the central directory and end records follow on
/// [`StreamingZipWriter::finish`], with the zip64 extras and end records `_write_end_record`
/// writes past `ZIP64_LIMIT` and `ZIP_FILECOUNT_LIMIT`.
///
/// Offsets are the stream's own positions, starting where it stands when the writer is made,
/// as CPython 3.12's `start_dir = fp.tell()` and `header_offset = fp.tell()` are (it has no
/// `_start_disk`). ZIP64 is always allowed, as the export allows it, so CPython's
/// `LargeZipFile` refusals cannot arise.
pub struct StreamingZipWriter<W: std::io::Write + std::io::Seek> {
    out: W,
    start_dir: u64,
    written: Vec<StreamedMember>,
}

/// One member open for writing (`_ZipWriteFile`).
pub struct StreamedMemberWriter<'a, W: std::io::Write + std::io::Seek> {
    writer: &'a mut StreamingZipWriter<W>,
    member: ZipMember,
    header_offset: u64,
    zip64: bool,
    size: u64,
    crc: Crc,
}

/// `ZipInfo.FileHeader(zip64)` for a stored member with no extra of its own.
fn stored_file_header(member: &ZipMember, crc: u32, size: u64, zip64: bool) -> Vec<u8> {
    let (dosdate, dostime) = dos(member.date_time);
    let (filename, flag_bits) = encode_filename(&member.filename, 0);
    let (mut compress_size, mut file_size) = (size, size);
    let mut extra = Vec::new();
    let mut version = DEFAULT_VERSION;
    if zip64 {
        extra.extend_from_slice(&1u16.to_le_bytes());
        extra.extend_from_slice(&16u16.to_le_bytes());
        extra.extend_from_slice(&file_size.to_le_bytes());
        extra.extend_from_slice(&compress_size.to_le_bytes());
        file_size = 0xFFFF_FFFF;
        compress_size = 0xFFFF_FFFF;
        version = ZIP64_VERSION;
    }
    let mut header = Vec::with_capacity(30 + filename.len() + extra.len());
    header.extend_from_slice(b"PK\x03\x04");
    header.push(version); // extract_version
    header.push(0); // reserved
    header.extend_from_slice(&flag_bits.to_le_bytes());
    header.extend_from_slice(&member.compress_type.to_le_bytes());
    header.extend_from_slice(&dostime.to_le_bytes());
    header.extend_from_slice(&dosdate.to_le_bytes());
    header.extend_from_slice(&crc.to_le_bytes());
    header.extend_from_slice(&(compress_size as u32).to_le_bytes());
    header.extend_from_slice(&(file_size as u32).to_le_bytes());
    header.extend_from_slice(&(filename.len() as u16).to_le_bytes());
    header.extend_from_slice(&(extra.len() as u16).to_le_bytes());
    header.extend_from_slice(filename);
    header.extend_from_slice(&extra);
    header
}

impl<W: std::io::Write + std::io::Seek> StreamingZipWriter<W> {
    /// `ZipFile(out, "w")`: the archive starts where the stream stands.
    pub fn new(mut out: W) -> std::io::Result<Self> {
        let start_dir = out.stream_position()?;
        out.seek(std::io::SeekFrom::Start(start_dir))?;
        Ok(Self { out, start_dir, written: Vec::new() })
    }

    /// `open(info, "w", force_zip64=False)` for a stored member whose `file_size` is set to
    /// `file_size` beforehand: its provisional header is written now.
    pub fn open_member(
        &mut self,
        member: ZipMember,
        file_size: u64,
    ) -> Result<StreamedMemberWriter<'_, W>, ZipWriteError> {
        if member.compress_type != ZIP_STORED {
            return Err(ZipWriteError::Runtime("The streaming writer writes stored members only"));
        }
        let mut member = member;
        if member.external_attr == 0 {
            member.external_attr = 0o600 << 16;
        }
        // `zip64 = force_zip64 or (zinfo.file_size * 1.05 > ZIP64_LIMIT)`.
        let zip64 = file_size as f64 * 1.05 > ZIP64_LIMIT as f64;
        self.out.seek(std::io::SeekFrom::Start(self.start_dir))?;
        let header_offset = self.out.stream_position()?;
        // `FileHeader` with `CRC = 0` and `compress_size = 0` but the preset `file_size`.
        let mut provisional = stored_file_header(&member, 0, file_size, zip64);
        if !zip64 {
            provisional[18..22].copy_from_slice(&0u32.to_le_bytes());
        } else {
            let extra_start = provisional.len() - 16;
            provisional[extra_start + 8..extra_start + 16].copy_from_slice(&0u64.to_le_bytes());
        }
        self.out.write_all(&provisional)?;
        Ok(StreamedMemberWriter { writer: self, member, header_offset, zip64, size: 0, crc: Crc::new() })
    }

    /// `close()`: `_write_end_record()` and a flush; the stream is handed back.
    pub fn finish(mut self) -> std::io::Result<W> {
        self.out.seek(std::io::SeekFrom::Start(self.start_dir))?;
        let mut central = Vec::new();
        for written in &self.written {
            let member = &written.member;
            let (dosdate, dostime) = dos(member.date_time);
            let (filename, flag_bits) = encode_filename(&member.filename, 0);
            let mut extra: Vec<u64> = Vec::new();
            let (mut file_size, mut compress_size) = (written.size, written.size);
            if written.size > ZIP64_LIMIT {
                extra.push(written.size);
                extra.push(written.size);
                file_size = 0xFFFF_FFFF;
                compress_size = 0xFFFF_FFFF;
            }
            let mut header_offset = written.header_offset;
            if written.header_offset > ZIP64_LIMIT {
                extra.push(written.header_offset);
                header_offset = 0xFFFF_FFFF;
            }
            let mut extra_data = Vec::new();
            let mut min_version = 0u8;
            if !extra.is_empty() {
                extra_data.extend_from_slice(&1u16.to_le_bytes());
                extra_data.extend_from_slice(&((8 * extra.len()) as u16).to_le_bytes());
                for value in &extra {
                    extra_data.extend_from_slice(&value.to_le_bytes());
                }
                min_version = ZIP64_VERSION;
            }
            // `FileHeader` already raised both versions to `ZIP64_VERSION` for a zip64 member.
            let member_version = if written.zip64 { ZIP64_VERSION } else { DEFAULT_VERSION };
            let version = min_version.max(member_version);
            central.extend_from_slice(b"PK\x01\x02");
            central.push(version); // create_version
            central.push(member.create_system);
            central.push(version); // extract_version
            central.push(0); // reserved
            central.extend_from_slice(&flag_bits.to_le_bytes());
            central.extend_from_slice(&member.compress_type.to_le_bytes());
            central.extend_from_slice(&dostime.to_le_bytes());
            central.extend_from_slice(&dosdate.to_le_bytes());
            central.extend_from_slice(&written.crc.to_le_bytes());
            central.extend_from_slice(&(compress_size as u32).to_le_bytes());
            central.extend_from_slice(&(file_size as u32).to_le_bytes());
            central.extend_from_slice(&(filename.len() as u16).to_le_bytes());
            central.extend_from_slice(&(extra_data.len() as u16).to_le_bytes());
            central.extend_from_slice(&0u16.to_le_bytes()); // comment length
            central.extend_from_slice(&0u16.to_le_bytes()); // disk number
            central.extend_from_slice(&0u16.to_le_bytes()); // internal_attr
            central.extend_from_slice(&member.external_attr.to_le_bytes());
            central.extend_from_slice(&(header_offset as u32).to_le_bytes());
            central.extend_from_slice(filename);
            central.extend_from_slice(&extra_data);
        }
        self.out.write_all(&central)?;
        let pos2 = self.out.stream_position()?;
        let mut count = self.written.len() as u64;
        let mut size = pos2 - self.start_dir;
        let mut offset = self.start_dir;
        if count > ZIP_FILECOUNT_LIMIT as u64 || offset > ZIP64_LIMIT || size > ZIP64_LIMIT {
            let mut record = Vec::with_capacity(76);
            record.extend_from_slice(b"PK\x06\x06");
            record.extend_from_slice(&44u64.to_le_bytes());
            record.extend_from_slice(&45u16.to_le_bytes());
            record.extend_from_slice(&45u16.to_le_bytes());
            record.extend_from_slice(&0u32.to_le_bytes());
            record.extend_from_slice(&0u32.to_le_bytes());
            record.extend_from_slice(&count.to_le_bytes());
            record.extend_from_slice(&count.to_le_bytes());
            record.extend_from_slice(&size.to_le_bytes());
            record.extend_from_slice(&offset.to_le_bytes());
            record.extend_from_slice(b"PK\x06\x07");
            record.extend_from_slice(&0u32.to_le_bytes());
            record.extend_from_slice(&pos2.to_le_bytes());
            record.extend_from_slice(&1u32.to_le_bytes());
            self.out.write_all(&record)?;
            count = count.min(0xFFFF);
            size = size.min(0xFFFF_FFFF);
            offset = offset.min(0xFFFF_FFFF);
        }
        let mut end = Vec::with_capacity(22);
        end.extend_from_slice(b"PK\x05\x06");
        end.extend_from_slice(&0u16.to_le_bytes());
        end.extend_from_slice(&0u16.to_le_bytes());
        end.extend_from_slice(&(count as u16).to_le_bytes());
        end.extend_from_slice(&(count as u16).to_le_bytes());
        end.extend_from_slice(&(size as u32).to_le_bytes());
        end.extend_from_slice(&(offset as u32).to_le_bytes());
        end.extend_from_slice(&0u16.to_le_bytes()); // comment length
        self.out.write_all(&end)?;
        self.out.flush()?;
        Ok(self.out)
    }
}

impl<W: std::io::Write + std::io::Seek> StreamedMemberWriter<'_, W> {
    /// `write(data)`.
    pub fn write(&mut self, data: &[u8]) -> std::io::Result<()> {
        self.size += data.len() as u64;
        self.crc.update(data);
        self.writer.out.write_all(data)
    }

    /// `close()`: the size checks, then the header rewritten with the real CRC and sizes.
    pub fn close(self) -> Result<(), ZipWriteError> {
        if !self.zip64 && self.size > ZIP64_LIMIT {
            return Err(ZipWriteError::Runtime("File size too large, try using force_zip64"));
        }
        let out = &mut self.writer.out;
        let start_dir = out.stream_position()?;
        out.seek(std::io::SeekFrom::Start(self.header_offset))?;
        out.write_all(&stored_file_header(&self.member, self.crc.sum(), self.size, self.zip64))?;
        out.seek(std::io::SeekFrom::Start(start_dir))?;
        self.writer.start_dir = start_dir;
        self.writer.written.push(StreamedMember {
            member: self.member,
            crc: self.crc.sum(),
            size: self.size,
            header_offset: self.header_offset,
            zip64: self.zip64,
        });
        Ok(())
    }
}
