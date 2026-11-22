//! `StreamingZipWriter` against CPython 3.12.11 (`tests/vectors/zip_stream_vectors.json`, written
//! by `generate_zip_stream_vectors.py`): every archive is rebuilt from the same members and must
//! be CPython's bytes from its bias on, then read back with `ZipArchive`.
//!
//! The archives CPython wrote into a sparse file positioned near 2 GiB are written here through a
//! stream whose positions start at the bias (no file, no disk), and read back through a source
//! that maps position `bias + n` to byte `n`, so their header offsets really are past
//! `ZIP64_LIMIT`.

use serde_json::Value as Json;
use std::io::{Seek, SeekFrom, Write};
use sylvia_base::hash::sha256_hex;
use sylvia_domain::package_archive::zipfile::{ZipArchive, ZipError, ZipSource, ZIP_STORED};
use sylvia_domain::package_archive::zipfile_write::{StreamingZipWriter, ZipMember};

/// A stream whose positions start at `bias`: what is written lands in `data` at `position - bias`.
struct Biased {
    bias: u64,
    data: Vec<u8>,
    position: u64,
}

impl Write for Biased {
    fn write(&mut self, buffer: &[u8]) -> std::io::Result<usize> {
        let start = (self.position - self.bias) as usize;
        let end = start + buffer.len();
        if self.data.len() < end {
            self.data.resize(end, 0);
        }
        self.data[start..end].copy_from_slice(buffer);
        self.position += buffer.len() as u64;
        Ok(buffer.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl Seek for Biased {
    fn seek(&mut self, to: SeekFrom) -> std::io::Result<u64> {
        let SeekFrom::Start(position) = to else { panic!("the writer seeks only from the start") };
        assert!(position >= self.bias, "the writer never seeks before its start");
        self.position = position;
        Ok(position)
    }
    fn stream_position(&mut self) -> std::io::Result<u64> {
        Ok(self.position)
    }
}

/// The same archive as a file read by position: zeros before `bias`, then `data`.
#[derive(Debug)]
struct Mapped {
    bias: u64,
    data: Vec<u8>,
}

impl ZipSource for Mapped {
    fn len(&self) -> u64 {
        self.bias + self.data.len() as u64
    }
    fn read_at(&self, offset: u64, count: usize) -> Result<Vec<u8>, ZipError> {
        let end = (offset + count as u64).min(self.len());
        Ok((offset..end)
            .map(|position| if position < self.bias { 0 } else { self.data[(position - self.bias) as usize] })
            .collect())
    }
}

fn content(index: usize, size: usize) -> Vec<u8> {
    (0..size).map(|offset| ((index + offset) % 251) as u8).collect()
}

/// Each member's name, the bytes written, and the `file_size` preset before `open` (the bytes
/// written unless the vector gives a third value).
fn members(case: &Json) -> Vec<(String, usize, u64)> {
    match &case["members"] {
        Json::Array(items) => items
            .iter()
            .map(|item| {
                let size = item[1].as_u64().unwrap();
                (item[0].as_str().unwrap().to_owned(), size as usize, item[2].as_u64().unwrap_or(size))
            })
            .collect(),
        pattern => {
            assert_eq!(pattern["pattern"], "m/{index:05d}");
            let size = pattern["size"].as_u64().unwrap() as usize;
            (0..pattern["count"].as_u64().unwrap() as usize)
                .map(|index| (format!("m/{index:05}"), size, size as u64))
                .collect()
        }
    }
}

#[test]
fn streamed_archives_are_cpythons_bytes() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/zip_stream_vectors.json");
    let record: Json = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases = record["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 8);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let bias = case["bias"].as_u64().unwrap();
        let members = members(case);
        let mut writer = StreamingZipWriter::new(Biased { bias, data: Vec::new(), position: bias }).unwrap();
        for (index, (member_name, size, preset)) in members.iter().enumerate() {
            let member = ZipMember {
                filename: member_name.clone(),
                date_time: (1980, 1, 1, 0, 0, 0),
                compress_type: ZIP_STORED,
                create_system: 3,
                external_attr: 0o100600 << 16,
            };
            let data = content(index, *size);
            let mut open = writer.open_member(member, *preset).unwrap();
            for chunk in data.chunks(65_536) {
                open.write(chunk).unwrap();
            }
            open.close().unwrap();
        }
        let written = writer.finish().unwrap().data;
        assert_eq!(written.len() as u64, case["length"].as_u64().unwrap(), "{name}: length");
        assert_eq!(sha256_hex(&written), case["sha256"].as_str().unwrap(), "{name}: bytes");
        // Read back from the biased positions CPython wrote at.
        let archive = ZipArchive::open_source(Box::new(Mapped { bias, data: written })).unwrap();
        assert_eq!(archive.infos().len(), members.len(), "{name}: members");
        for (index, (info, (member_name, size, _))) in archive.infos().iter().zip(&members).enumerate() {
            assert_eq!(&info.filename, member_name, "{name}");
            assert!(info.header_offset >= bias as i64, "{name}: offsets are the stream's own");
            let mut read = Vec::new();
            archive
                .read_chunks(info, 65_536, |chunk| {
                    read.extend_from_slice(chunk);
                    Ok::<(), ()>(())
                })
                .unwrap();
            assert_eq!(read, content(index, *size), "{name}: {member_name}");
        }
        if name == "offset_past_limit" {
            assert!(archive.infos()[1].header_offset > (1 << 31) - 1, "past ZIP64_LIMIT");
        }
    }
}
