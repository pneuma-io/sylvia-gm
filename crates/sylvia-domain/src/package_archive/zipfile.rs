//! CPython 3.12.11's `zipfile` read path, translated (see docs/rust-port/third-party-ports.md).
//!
//! Package admission shows `str(error)` of a `zipfile` failure to the player, so this follows
//! the library's source rather than only its results: `_EndRecData` / `_EndRecData64`,
//! `ZipFile._RealGetContents`, `ZipInfo._decodeExtra`, the local-header checks of
//! `ZipFile.open` and `ZipExtFile.read`, with the same messages and the same order of checks.
//! Raw DEFLATE (`zlib.decompressobj(-15)`) is `flate2`; only stored and DEFLATE members are
//! read (package admission refuses every other method before reading).
//!
//! The archive is read through a [`ZipSource`]: an in-memory spool ([`ZipArchive::open`], package
//! admission's) or a file read by position ([`ZipArchive::open_file`], portable campaign archive
//! validation's, which must not hold an archive of up to 8 GiB in memory). Python's spool is a
//! `BytesIO` until it passes 256 KiB, and a `BytesIO` clamps a negative end-relative seek to 0
//! where a real file raises `OSError`. Both sources clamp here. For admission the directory
//! preflight (`zip_bounds`) makes the difference unreachable; campaign archive validation reports
//! either outcome of a failed open as the same refusal.
//!
//! Two behaviours depend on the platform, as in CPython: on Windows `_sanitize_filename`
//! turns `\` into `/`, and `is_dir` also accepts a trailing `\`.

use super::cp437_table::CP437_HIGH;
use crate::pyops::{py_bytes_repr, py_utf8_decode};
use flate2::{Crc, Decompress, FlushDecompress, Status};
use sylvia_base::pystr::repr;

/// The exception a `zipfile` read raises: its class decides whether package admission
/// reports it (`archive_invalid`) or lets it escape as an internal error.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ZipError {
    /// `zipfile.BadZipFile(message)`.
    BadZip(String),
    /// `NotImplementedError(message)` (a `RuntimeError` subclass).
    NotImplemented(String),
    /// `RuntimeError(message)`.
    Runtime(String),
    /// `UnicodeDecodeError` (a `ValueError` subclass), with its `str()`.
    UnicodeDecode(String),
    /// `EOFError()`: the archive ends inside a member.
    Eof,
    /// `zlib.error(message)`: corrupt DEFLATE data.
    Zlib(String),
    /// `OSError(message)`: reading a file-backed archive failed.
    Os(String),
}

impl ZipError {
    /// `type(error).__name__`.
    pub fn python_class(&self) -> &'static str {
        match self {
            ZipError::BadZip(_) => "BadZipFile",
            ZipError::NotImplemented(_) => "NotImplementedError",
            ZipError::Runtime(_) => "RuntimeError",
            ZipError::UnicodeDecode(_) => "UnicodeDecodeError",
            ZipError::Eof => "EOFError",
            ZipError::Zlib(_) => "error",
            ZipError::Os(_) => "OSError",
        }
    }

    /// `str(error)`.
    pub fn message(&self) -> &str {
        match self {
            ZipError::BadZip(m)
            | ZipError::NotImplemented(m)
            | ZipError::Runtime(m)
            | ZipError::UnicodeDecode(m)
            | ZipError::Zlib(m)
            | ZipError::Os(m) => m,
            ZipError::Eof => "",
        }
    }
}

type ZipResult<T> = Result<T, ZipError>;

fn bad(message: impl Into<String>) -> ZipError {
    ZipError::BadZip(message.into())
}

const SIZE_END_CENT_DIR: usize = 22;
const SIZE_END_CENT_DIR64_LOCATOR: usize = 20;
const SIZE_END_CENT_DIR64: usize = 56;
const SIZE_CENTRAL_DIR: usize = 46;
const SIZE_FILE_HEADER: usize = 30;
const ZIP_MAX_COMMENT: usize = (1 << 16) - 1;
const MAX_EXTRACT_VERSION: u8 = 63;
const MASK_ENCRYPTED: u16 = 1 << 0;
const MASK_COMPRESSED_PATCH: u16 = 1 << 5;
const MASK_STRONG_ENCRYPTION: u16 = 1 << 6;
const MASK_UTF_FILENAME: u16 = 1 << 11;
/// `zipfile.ZIP_STORED` and `ZIP_DEFLATED`.
pub const ZIP_STORED: u16 = 0;
pub const ZIP_DEFLATED: u16 = 8;
const MIN_READ_SIZE: i64 = 4096;

fn u16_at(data: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([data[at], data[at + 1]])
}
fn u32_at(data: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(data[at..at + 4].try_into().expect("four bytes"))
}
fn u64_at(data: &[u8], at: usize) -> u64 {
    u64::from_le_bytes(data[at..at + 8].try_into().expect("eight bytes"))
}

/// Random access to an archive's bytes. `len` is taken once, when the archive is opened (as
/// CPython's `fp.seek(0, 2)` in `_EndRecData`), so a file that grows afterwards does not change
/// it. `read_at` returns up to `count` bytes from `offset`, fewer only at the end, as `fp.read(n)`
/// does; nothing relies on a file cursor.
pub trait ZipSource: std::fmt::Debug {
    /// The byte length of the source.
    fn len(&self) -> u64;
    /// Whether the source has no bytes.
    fn is_empty(&self) -> bool {
        self.len() == 0
    }
    /// Up to `count` bytes from `offset`, fewer only at the end.
    fn read_at(&self, offset: u64, count: usize) -> ZipResult<Vec<u8>>;
}

/// An in-memory spool.
#[derive(Debug)]
struct SliceSource<'a>(&'a [u8]);

impl ZipSource for SliceSource<'_> {
    fn len(&self) -> u64 {
        self.0.len() as u64
    }
    fn read_at(&self, offset: u64, count: usize) -> ZipResult<Vec<u8>> {
        let start = usize::try_from(offset).unwrap_or(usize::MAX).min(self.0.len());
        let end = start.saturating_add(count).min(self.0.len());
        Ok(self.0[start..end].to_vec())
    }
}

/// A file read by position (`FileExt::seek_read` on Windows, `FileExt::read_at` elsewhere).
/// Neither promises a full read, so each call loops until it has `count` bytes or reaches the
/// end. On Windows `seek_read` also moves the file cursor; nothing here relies on it.
#[derive(Debug)]
struct FileSource<'a> {
    file: &'a std::fs::File,
    len: u64,
}

impl FileSource<'_> {
    fn read_once(&self, offset: u64, buffer: &mut [u8]) -> std::io::Result<usize> {
        #[cfg(windows)]
        {
            std::os::windows::fs::FileExt::seek_read(self.file, buffer, offset)
        }
        #[cfg(unix)]
        {
            std::os::unix::fs::FileExt::read_at(self.file, buffer, offset)
        }
    }
}

impl ZipSource for FileSource<'_> {
    fn len(&self) -> u64 {
        self.len
    }
    fn read_at(&self, offset: u64, count: usize) -> ZipResult<Vec<u8>> {
        if offset >= self.len {
            return Ok(Vec::new());
        }
        let wanted = count.min(usize::try_from(self.len - offset).unwrap_or(usize::MAX));
        let mut buffer = vec![0u8; wanted];
        let mut filled = 0;
        while filled < wanted {
            match self.read_once(offset + filled as u64, &mut buffer[filled..]) {
                Ok(0) => break,
                Ok(read) => filled += read,
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
                Err(error) => return Err(ZipError::Os(error.to_string())),
            }
        }
        buffer.truncate(filled);
        Ok(buffer)
    }
}

/// A seekable view of the source with `BytesIO` semantics.
struct Spool<'a> {
    source: &'a dyn ZipSource,
    len: u64,
    position: u64,
}

impl<'a> Spool<'a> {
    fn new(source: &'a dyn ZipSource) -> Self {
        Self { source, len: source.len(), position: 0 }
    }
    /// `seek(offset, 0)`.
    fn seek_start(&mut self, offset: u64) {
        self.position = offset;
    }
    /// `seek(offset, 2)`: `BytesIO` clamps a position before the start to 0.
    fn seek_end(&mut self, offset: i64) {
        let target = self.len as i64 + offset;
        self.position = target.max(0) as u64;
    }
    fn read(&mut self, count: usize) -> ZipResult<Vec<u8>> {
        if self.position >= self.len {
            return Ok(Vec::new());
        }
        let data = self.source.read_at(self.position, count)?;
        self.position += data.len() as u64;
        Ok(data)
    }
}

/// `bytes.decode("cp437")`.
fn cp437(data: &[u8]) -> String {
    data.iter().map(|&b| if b < 0x80 { b as char } else { CP437_HIGH[(b - 0x80) as usize] }).collect()
}

fn decode_name(data: &[u8], utf8: bool) -> ZipResult<String> {
    if utf8 {
        py_utf8_decode(data).map_err(ZipError::UnicodeDecode)
    } else {
        Ok(cp437(data))
    }
}

/// `_sanitize_filename(filename)`.
fn sanitize_filename(filename: &str) -> String {
    let filename = match filename.find('\0') {
        Some(index) => &filename[..index],
        None => filename,
    };
    if cfg!(windows) {
        filename.replace('\\', "/")
    } else {
        filename.to_owned()
    }
}

/// One member's central directory entry (`ZipInfo`), with the fields admission reads.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ZipInfo {
    /// `orig_filename`: the decoded name before sanitizing.
    pub orig_filename: String,
    /// `filename`: sanitized, or from a valid Unicode path extra field.
    pub filename: String,
    pub flag_bits: u16,
    pub compress_type: u16,
    pub crc: u32,
    pub compress_size: u64,
    pub file_size: u64,
    pub header_offset: i64,
    pub external_attr: u32,
    /// `date_time`: `((d>>9)+1980, (d>>5)&0xF, d&0x1F, t>>11, (t>>5)&0x3F, (t&0x1F)*2)` from
    /// the central header's time and date, unchecked (month 0 and second 62 are possible).
    pub date_time: (u16, u8, u8, u8, u8, u8),
    /// `create_system`: the central header's byte at offset 5.
    pub create_system: u8,
    /// `internal_attr`: the central header's 16 bits at offset 36.
    pub internal_attr: u16,
    /// `extra`: the raw extra field (`_decodeExtra` reads it but leaves it unchanged).
    pub extra: Vec<u8>,
    /// `comment`: the raw member comment.
    pub comment: Vec<u8>,
    end_offset: Option<i64>,
}

impl ZipInfo {
    /// `is_dir()`.
    pub fn is_dir(&self) -> bool {
        self.filename.ends_with('/') || (cfg!(windows) && self.filename.ends_with('\\'))
    }

    /// `_decodeExtra(filename_crc)`.
    fn decode_extra(&mut self, mut extra: &[u8], filename_crc: u32) -> ZipResult<()> {
        while extra.len() >= 4 {
            let tp = u16_at(extra, 0);
            let ln = u16_at(extra, 2) as usize;
            if ln + 4 > extra.len() {
                return Err(bad(format!("Corrupt extra field {tp:04x} (size={ln})")));
            }
            let mut data = &extra[4..ln + 4];
            if tp == 0x0001 {
                let mut take = |field: &str| -> ZipResult<u64> {
                    if data.len() < 8 {
                        return Err(bad(format!("Corrupt zip64 extra field. {field} not found.")));
                    }
                    let value = u64_at(data, 0);
                    data = &data[8..];
                    Ok(value)
                };
                if self.file_size == 0xFFFF_FFFF_FFFF_FFFF || self.file_size == 0xFFFF_FFFF {
                    self.file_size = take("File size")?;
                }
                if self.compress_size == 0xFFFF_FFFF {
                    self.compress_size = take("Compress size")?;
                }
                if self.header_offset == 0xFFFF_FFFF {
                    self.header_offset = take("Header offset")? as i64;
                }
            } else if tp == 0x7075 {
                if data.len() < 5 {
                    return Err(bad("Corrupt unicode path extra field (0x7075)"));
                }
                let (version, name_crc) = (data[0], u32_at(data, 1));
                if version == 1 && name_crc == filename_crc {
                    let name = py_utf8_decode(&data[5..])
                        .map_err(|_| bad("Corrupt unicode path extra field (0x7075): invalid utf-8 bytes"))?;
                    if !name.is_empty() {
                        self.filename = sanitize_filename(&name);
                    }
                }
            }
            extra = &extra[ln + 4..];
        }
        Ok(())
    }
}

/// One opened archive (`zipfile.ZipFile(spool, "r")`).
#[derive(Debug)]
pub struct ZipArchive<'a> {
    source: Box<dyn ZipSource + 'a>,
    infos: Vec<ZipInfo>,
    comment: Vec<u8>,
}

/// The end-of-central-directory fields `_RealGetContents` reads.
struct EndRecord {
    signature64: bool,
    size: u64,
    offset: u64,
    location: i64,
    /// `endrec[_ECD_COMMENT]`, always from the plain end record.
    comment: Vec<u8>,
}

/// `_EndRecData64(fpin, offset, endrec)`.
fn end_record64(spool: &mut Spool<'_>, offset: i64, record: EndRecord) -> ZipResult<EndRecord> {
    spool.seek_end(offset - SIZE_END_CENT_DIR64_LOCATOR as i64);
    let data = spool.read(SIZE_END_CENT_DIR64_LOCATOR)?;
    if data.len() != SIZE_END_CENT_DIR64_LOCATOR || &data[..4] != b"PK\x06\x07" {
        return Ok(record);
    }
    let (diskno, disks) = (u32_at(&data, 4), u32_at(&data, 16));
    if diskno != 0 || disks > 1 {
        return Err(bad("zipfiles that span multiple disks are not supported"));
    }
    spool.seek_end(offset - (SIZE_END_CENT_DIR64_LOCATOR + SIZE_END_CENT_DIR64) as i64);
    let data = spool.read(SIZE_END_CENT_DIR64)?;
    if data.len() != SIZE_END_CENT_DIR64 || &data[..4] != b"PK\x06\x06" {
        return Ok(record);
    }
    Ok(EndRecord {
        signature64: true,
        size: u64_at(&data, 40),
        offset: u64_at(&data, 48),
        location: record.location,
        comment: record.comment,
    })
}

/// `_EndRecData(fpin)`.
fn end_record(spool: &mut Spool<'_>) -> ZipResult<Option<EndRecord>> {
    let filesize = spool.len as usize;
    spool.seek_end(-(SIZE_END_CENT_DIR as i64));
    let data = spool.read(SIZE_END_CENT_DIR)?;
    if data.len() == SIZE_END_CENT_DIR && &data[..4] == b"PK\x05\x06" && data[20..] == [0, 0] {
        let record = EndRecord {
            signature64: false,
            size: u32_at(&data, 12) as u64,
            offset: u32_at(&data, 16) as u64,
            location: (filesize - SIZE_END_CENT_DIR) as i64,
            comment: Vec::new(),
        };
        return end_record64(spool, -(SIZE_END_CENT_DIR as i64), record).map(Some);
    }
    let max_comment_start = filesize.saturating_sub(ZIP_MAX_COMMENT + SIZE_END_CENT_DIR);
    spool.seek_start(max_comment_start as u64);
    let data = spool.read(ZIP_MAX_COMMENT + SIZE_END_CENT_DIR)?;
    let Some(start) = data.windows(4).rposition(|window| window == b"PK\x05\x06") else { return Ok(None) };
    let record_data = &data[start..(start + SIZE_END_CENT_DIR).min(data.len())];
    if record_data.len() != SIZE_END_CENT_DIR {
        return Ok(None);
    }
    // `data[start+sizeEndCentDir:start+sizeEndCentDir+commentSize]`, clamped as a slice is.
    let comment_size = u16_at(record_data, 20) as usize;
    let comment_start = (start + SIZE_END_CENT_DIR).min(data.len());
    let comment = data[comment_start..(comment_start + comment_size).min(data.len())].to_vec();
    let record = EndRecord {
        signature64: false,
        size: u32_at(record_data, 12) as u64,
        offset: u32_at(record_data, 16) as u64,
        location: (max_comment_start + start) as i64,
        comment,
    };
    end_record64(spool, (max_comment_start + start) as i64 - filesize as i64, record).map(Some)
}

impl<'a> ZipArchive<'a> {
    /// `ZipFile(fp, "r")` on an in-memory spool: read the central directory.
    pub fn open(data: &'a [u8]) -> ZipResult<Self> {
        Self::open_source(Box::new(SliceSource(data)))
    }

    /// `ZipFile(path, "r")`: read the central directory of an open file, reading it by position
    /// from then on instead of holding it in memory.
    pub fn open_file(file: &'a std::fs::File) -> ZipResult<Self> {
        let len = file.metadata().map_err(|error| ZipError::Os(error.to_string()))?.len();
        Self::open_source(Box::new(FileSource { file, len }))
    }

    fn open_source(source: Box<dyn ZipSource + 'a>) -> ZipResult<Self> {
        let mut spool = Spool::new(source.as_ref());
        let record = end_record(&mut spool)?.ok_or_else(|| bad("File is not a zip file"))?;
        let mut concat = record.location - record.size as i64 - record.offset as i64;
        if record.signature64 {
            concat -= (SIZE_END_CENT_DIR64 + SIZE_END_CENT_DIR64_LOCATOR) as i64;
        }
        let start_dir = record.offset as i64 + concat;
        if start_dir < 0 {
            return Err(bad("Bad offset for central directory"));
        }
        spool.seek_start(start_dir as u64);
        let directory = spool.read(record.size as usize)?;
        let directory_source = SliceSource(&directory);
        let mut cursor = Spool::new(&directory_source);
        let mut infos = Vec::new();
        let mut total: u64 = 0;
        while total < record.size {
            let header = cursor.read(SIZE_CENTRAL_DIR)?;
            if header.len() != SIZE_CENTRAL_DIR {
                return Err(bad("Truncated central directory"));
            }
            if &header[..4] != b"PK\x01\x02" {
                return Err(bad("Bad magic number for central directory"));
            }
            let name_length = u16_at(&header, 28) as usize;
            let extra_length = u16_at(&header, 30) as usize;
            let comment_length = u16_at(&header, 32) as usize;
            let raw_name = cursor.read(name_length)?;
            let mut crc = Crc::new();
            crc.update(&raw_name);
            let flag_bits = u16_at(&header, 8);
            let orig_filename = decode_name(&raw_name, flag_bits & MASK_UTF_FILENAME != 0)?;
            let extra = cursor.read(extra_length)?;
            let comment = cursor.read(comment_length)?;
            let (time, date) = (u16_at(&header, 12), u16_at(&header, 14));
            let extract_version = header[6];
            let mut info = ZipInfo {
                filename: sanitize_filename(&orig_filename),
                orig_filename,
                flag_bits,
                compress_type: u16_at(&header, 10),
                crc: u32_at(&header, 16),
                compress_size: u32_at(&header, 20) as u64,
                file_size: u32_at(&header, 24) as u64,
                header_offset: u32_at(&header, 42) as i64,
                external_attr: u32_at(&header, 38),
                date_time: (
                    (date >> 9) + 1980,
                    ((date >> 5) & 0xF) as u8,
                    (date & 0x1F) as u8,
                    (time >> 11) as u8,
                    ((time >> 5) & 0x3F) as u8,
                    ((time & 0x1F) * 2) as u8,
                ),
                create_system: header[5],
                internal_attr: u16_at(&header, 36),
                extra: extra.clone(),
                comment,
                end_offset: None,
            };
            if extract_version > MAX_EXTRACT_VERSION {
                return Err(ZipError::NotImplemented(format!(
                    "zip file version {:.1}",
                    f64::from(extract_version) / 10.0
                )));
            }
            info.decode_extra(&extra, crc.sum())?;
            info.header_offset += concat;
            infos.push(info);
            total += (SIZE_CENTRAL_DIR + name_length + extra_length + comment_length) as u64;
        }
        // `for zinfo in reversed(sorted(filelist, key=header_offset))`: a stable sort, reversed.
        let mut order: Vec<usize> = (0..infos.len()).collect();
        order.sort_by_key(|&index| infos[index].header_offset);
        let mut end_offset = start_dir;
        for &index in order.iter().rev() {
            infos[index].end_offset = Some(end_offset);
            end_offset = infos[index].header_offset;
        }
        Ok(Self { source, infos, comment: record.comment })
    }

    /// `comment`: the archive comment from the end record.
    pub fn comment(&self) -> &[u8] {
        &self.comment
    }

    /// `infolist()`.
    pub fn infos(&self) -> &[ZipInfo] {
        &self.infos
    }

    /// `ZipFile.open(info)` followed by `member.read(chunk)` until it returns nothing, calling
    /// `sink` with each chunk (Python's caller loop). `sink` may stop the read with its own error.
    pub fn read_chunks<E>(
        &self,
        info: &ZipInfo,
        chunk: usize,
        mut sink: impl FnMut(&[u8]) -> Result<(), E>,
    ) -> Result<(), ReadError<E>> {
        let mut member = self.open_member(info).map_err(ReadError::Zip)?;
        loop {
            let data = member.read(chunk as i64).map_err(ReadError::Zip)?;
            if data.is_empty() {
                return Ok(());
            }
            sink(&data).map_err(ReadError::Caller)?;
        }
    }

    /// `ZipFile.open(name)` for reading.
    fn open_member(&self, info: &ZipInfo) -> ZipResult<MemberReader<'_>> {
        let source = self.source.as_ref();
        let mut file = Shared { source, len: source.len(), position: info.header_offset.max(0) as u64 };
        let header = file.read(SIZE_FILE_HEADER)?;
        if header.len() != SIZE_FILE_HEADER {
            return Err(bad("Truncated file header"));
        }
        if &header[..4] != b"PK\x03\x04" {
            return Err(bad("Bad magic number for file header"));
        }
        let name = file.read(u16_at(&header, 26) as usize)?;
        let extra_length = u16_at(&header, 28) as usize;
        if extra_length > 0 {
            file.position += extra_length as u64;
        }
        if info.flag_bits & MASK_COMPRESSED_PATCH != 0 {
            return Err(ZipError::NotImplemented("compressed patched data (flag bit 5)".into()));
        }
        if info.flag_bits & MASK_STRONG_ENCRYPTION != 0 {
            return Err(ZipError::NotImplemented("strong encryption (flag bit 6)".into()));
        }
        let name_text = decode_name(&name, u16_at(&header, 6) & MASK_UTF_FILENAME != 0)?;
        if name_text != info.orig_filename {
            return Err(bad(format!(
                "File name in directory {} and header {} differ.",
                repr(&info.orig_filename),
                py_bytes_repr(&name)
            )));
        }
        if let Some(end_offset) = info.end_offset {
            if file.position as i64 + info.compress_size as i64 > end_offset
                && end_offset != info.header_offset
            {
                return Err(bad(format!(
                    "Overlapped entries: {} (possible zip bomb)",
                    repr(&info.orig_filename)
                )));
            }
        }
        if info.flag_bits & MASK_ENCRYPTED != 0 {
            return Err(ZipError::Runtime(format!(
                "File {} is encrypted, password required for extraction",
                repr(&info.filename)
            )));
        }
        let decompressor = match info.compress_type {
            ZIP_STORED => None,
            ZIP_DEFLATED => Some(Inflater::new()),
            // Python reads these; package admission refuses them before reading, and no
            // other port caller opens them yet.
            12 | 14 => {
                return Err(ZipError::NotImplemented(
                    "bzip2 and lzma members are not read by the port".into(),
                ))
            }
            _ => return Err(ZipError::NotImplemented("That compression method is not supported".into())),
        };
        Ok(MemberReader {
            file,
            compress_left: info.compress_size as i64,
            left: info.file_size as i64,
            eof: false,
            read_buffer: Vec::new(),
            offset: 0,
            crc: Crc::new(),
            expected_crc: info.crc,
            name: info.filename.clone(),
            decompressor,
        })
    }
}

/// An error from [`ZipArchive::read_chunks`]: the reader's, or the caller's sink.
#[derive(Debug)]
pub enum ReadError<E> {
    Zip(ZipError),
    Caller(E),
}

/// `_SharedFile`: a position into the source.
struct Shared<'a> {
    source: &'a dyn ZipSource,
    len: u64,
    position: u64,
}

impl Shared<'_> {
    fn read(&mut self, count: usize) -> ZipResult<Vec<u8>> {
        if self.position >= self.len {
            return Ok(Vec::new());
        }
        let data = self.source.read_at(self.position, count)?;
        self.position += data.len() as u64;
        Ok(data)
    }
}

/// `zlib.decompressobj(-15)` with `decompress(data, max_length)`, `unconsumed_tail`, `eof`
/// and `flush()`.
struct Inflater {
    inner: Decompress,
    unconsumed_tail: Vec<u8>,
    eof: bool,
}

impl Inflater {
    fn new() -> Self {
        Self { inner: Decompress::new(false), unconsumed_tail: Vec::new(), eof: false }
    }

    /// CPython's `zlib_error`: zlib's own message, or its fixed text for `Z_DATA_ERROR`. flate2's
    /// `Display` adds a prefix of its own, so the message is read directly.
    fn zlib_error(error: flate2::DecompressError) -> ZipError {
        let message = error.message().unwrap_or("invalid input data");
        ZipError::Zlib(format!("Error -3 while decompressing data: {message}"))
    }

    /// `decompress(data, max_length)`.
    fn decompress(&mut self, data: &[u8], max_length: usize) -> ZipResult<Vec<u8>> {
        if self.eof {
            self.unconsumed_tail.clear();
            return Ok(Vec::new());
        }
        // zlib inflates into an output buffer of exactly `max_length` bytes.
        let mut output = vec![0u8; max_length];
        let mut produced = 0usize;
        let mut consumed = 0usize;
        loop {
            let before_in = self.inner.total_in();
            let before_out = self.inner.total_out();
            let status = self
                .inner
                .decompress(&data[consumed..], &mut output[produced..], FlushDecompress::Sync)
                .map_err(Self::zlib_error)?;
            consumed += (self.inner.total_in() - before_in) as usize;
            produced += (self.inner.total_out() - before_out) as usize;
            let progressed = self.inner.total_in() != before_in || self.inner.total_out() != before_out;
            if status == Status::StreamEnd {
                self.eof = true;
                self.unconsumed_tail.clear();
                output.truncate(produced);
                return Ok(output);
            }
            if produced >= max_length || consumed >= data.len() || !progressed {
                break;
            }
        }
        self.unconsumed_tail = data[consumed..].to_vec();
        output.truncate(produced);
        Ok(output)
    }

    /// `flush()`: the rest of the output for the unconsumed tail, without a length limit.
    fn flush(&mut self) -> ZipResult<Vec<u8>> {
        let input = std::mem::take(&mut self.unconsumed_tail);
        let mut output = Vec::new();
        let mut consumed = 0usize;
        loop {
            output.reserve(16 * 1024);
            let before_in = self.inner.total_in();
            let before_out = self.inner.total_out();
            let status = self
                .inner
                .decompress_vec(&input[consumed..], &mut output, FlushDecompress::Finish)
                .map_err(Self::zlib_error)?;
            consumed += (self.inner.total_in() - before_in) as usize;
            if status == Status::StreamEnd {
                self.eof = true;
                return Ok(output);
            }
            if self.inner.total_in() == before_in && self.inner.total_out() == before_out {
                return Ok(output);
            }
        }
    }
}

/// `ZipExtFile` for a stored or DEFLATE member.
struct MemberReader<'a> {
    file: Shared<'a>,
    compress_left: i64,
    left: i64,
    eof: bool,
    read_buffer: Vec<u8>,
    offset: usize,
    crc: Crc,
    expected_crc: u32,
    name: String,
    decompressor: Option<Inflater>,
}

impl MemberReader<'_> {
    /// `read(n)`.
    fn read(&mut self, n: i64) -> ZipResult<Vec<u8>> {
        let end = n as usize + self.offset;
        if end < self.read_buffer.len() {
            let buffer = self.read_buffer[self.offset..end].to_vec();
            self.offset = end;
            return Ok(buffer);
        }
        let mut n = (end - self.read_buffer.len()) as i64;
        let mut buffer = self.read_buffer[self.offset..].to_vec();
        self.read_buffer.clear();
        self.offset = 0;
        while n > 0 && !self.eof {
            let data = self.read1(n)?;
            if (n as usize) < data.len() {
                buffer.extend_from_slice(&data[..n as usize]);
                self.read_buffer = data;
                self.offset = n as usize;
                break;
            }
            n -= data.len() as i64;
            buffer.extend_from_slice(&data);
        }
        Ok(buffer)
    }

    /// `_read1(n)`.
    fn read1(&mut self, n: i64) -> ZipResult<Vec<u8>> {
        if self.eof || n <= 0 {
            return Ok(Vec::new());
        }
        let mut data;
        if let Some(inflater) = &mut self.decompressor {
            data = std::mem::take(&mut inflater.unconsumed_tail);
            if n > data.len() as i64 {
                let more = self.read2(n - data.len() as i64)?;
                data.extend_from_slice(&more);
            }
        } else {
            data = self.read2(n)?;
        }
        if let Some(inflater) = &mut self.decompressor {
            let n = n.max(MIN_READ_SIZE) as usize;
            data = inflater.decompress(&data, n)?;
            self.eof = inflater.eof || (self.compress_left <= 0 && inflater.unconsumed_tail.is_empty());
            if self.eof {
                let rest = inflater.flush()?;
                data.extend_from_slice(&rest);
            }
        } else {
            self.eof = self.compress_left <= 0;
        }
        let keep = (self.left.max(0) as usize).min(data.len());
        data.truncate(keep);
        self.left -= data.len() as i64;
        if self.left <= 0 {
            self.eof = true;
        }
        self.update_crc(&data)?;
        Ok(data)
    }

    /// `_read2(n)`.
    fn read2(&mut self, n: i64) -> ZipResult<Vec<u8>> {
        if self.compress_left <= 0 {
            return Ok(Vec::new());
        }
        let n = n.max(MIN_READ_SIZE).min(self.compress_left);
        let data = self.file.read(n as usize)?;
        self.compress_left -= data.len() as i64;
        if data.is_empty() {
            return Err(ZipError::Eof);
        }
        Ok(data)
    }

    /// `_update_crc(newdata)`.
    fn update_crc(&mut self, data: &[u8]) -> ZipResult<()> {
        self.crc.update(data);
        if self.eof && self.crc.sum() != self.expected_crc {
            return Err(bad(format!("Bad CRC-32 for file {}", repr(&self.name))));
        }
        Ok(())
    }
}
