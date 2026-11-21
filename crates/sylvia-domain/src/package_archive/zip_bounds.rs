//! `infrastructure/zip_bounds.py`: bound the ZIP directory before reading untrusted bytes.

use super::zipfile::{ZipInfo, ZIP_DEFLATED, ZIP_STORED};
use crate::error::{DomainError, DomainResult};

/// `ZIP_DIRECTORY_BYTE_LIMIT`: a finite directory budget also bounds names, comments and extras.
pub const ZIP_DIRECTORY_BYTE_LIMIT: u64 = 8 * 1024 * 1024;

/// `validate_zip_compression(member)`: only codecs whose reader bounds each allocation.
pub fn validate_zip_compression(member: &ZipInfo) -> DomainResult<()> {
    if member.compress_type != ZIP_STORED && member.compress_type != ZIP_DEFLATED {
        return Err(DomainError::value(
            "Unsupported ZIP compression; re-save the archive using Store or Deflate",
        ));
    }
    Ok(())
}

fn u16_at(data: &[u8], at: usize) -> u64 {
    u64::from(u16::from_le_bytes([data[at], data[at + 1]]))
}
fn u32_at(data: &[u8], at: usize) -> u64 {
    u64::from(u32::from_le_bytes(data[at..at + 4].try_into().expect("four bytes")))
}
fn u64_at(data: &[u8], at: usize) -> u64 {
    u64::from_le_bytes(data[at..at + 8].try_into().expect("eight bytes"))
}

/// `preflight_zip_directory(source, maximum_members=..., maximum_directory_bytes=...)`: check the
/// directory bounds and count the actual headers without allocating member records.
pub fn preflight_zip_directory(
    data: &[u8],
    maximum_members: u64,
    maximum_directory_bytes: u64,
) -> DomainResult<()> {
    let invalid = || DomainError::value("Archive ZIP directory is invalid");
    let invalid64 = || DomainError::value("Archive ZIP64 directory is invalid");
    let size = data.len();
    let tail_size = size.min(65_557 + 20);
    let tail = &data[size - tail_size..];
    let Some(index) = tail.windows(4).rposition(|window| window == b"PK\x05\x06") else {
        return Err(invalid());
    };
    if index + 22 > tail.len() {
        return Err(invalid());
    }
    let (disk, directory_disk, on_disk) =
        (u16_at(tail, index + 4), u16_at(tail, index + 6), u16_at(tail, index + 8));
    let (mut count, mut length, mut offset) =
        (u16_at(tail, index + 10), u32_at(tail, index + 12), u32_at(tail, index + 16));
    let comment = u16_at(tail, index + 20) as usize;
    if disk != 0 || directory_disk != 0 || on_disk != count || index + 22 + comment != tail.len() {
        return Err(invalid());
    }
    let mut end = (size - tail_size + index) as u128;
    let has_zip64_locator = index >= 20 && &tail[index - 20..index - 16] == b"PK\x06\x07";
    if has_zip64_locator || count == 0xFFFF || length == 0xFFFF_FFFF || offset == 0xFFFF_FFFF {
        if index < 20 {
            return Err(invalid64());
        }
        let locator = &tail[index - 20..index];
        let (locator_disk, zip64_offset, disks) =
            (u32_at(locator, 4), u64_at(locator, 8), u32_at(locator, 16));
        if &locator[..4] != b"PK\x06\x07"
            || locator_disk != 0
            || disks != 1
            || u128::from(zip64_offset) + 56 > end - 20
        {
            return Err(invalid64());
        }
        let start = zip64_offset as usize;
        let record = data
            .get(start..start + 56)
            .ok_or_else(|| DomainError::value("Archive ZIP64 directory is truncated"))?;
        let record_size = u64_at(record, 4);
        let (disk64, directory_disk64) = (u32_at(record, 16), u32_at(record, 20));
        let (on_disk64, count64) = (u64_at(record, 24), u64_at(record, 32));
        if &record[..4] != b"PK\x06\x06"
            || record_size < 44
            || disk64 != 0
            || directory_disk64 != 0
            || on_disk64 != count64
            || u128::from(zip64_offset) + 12 + u128::from(record_size) != end - 20
        {
            return Err(invalid64());
        }
        count = count64;
        length = u64_at(record, 40);
        offset = u64_at(record, 48);
        end = u128::from(zip64_offset);
    }
    if count > maximum_members {
        return Err(DomainError::value("Archive has too many members"));
    }
    if length > maximum_directory_bytes {
        return Err(DomainError::value("Archive ZIP directory is too large"));
    }
    if u128::from(offset) + u128::from(length) != end {
        return Err(DomainError::value("Archive ZIP directory bounds are invalid"));
    }
    // ZipFile iterates headers rather than trusting the footer's count. Match that boundary:
    // a forged low footer count must not bypass the pre-allocation member limit.
    let end = end as usize;
    let mut position = offset as usize;
    let mut actual = 0u64;
    while position < end {
        let header = data.get(position..position + 46).filter(|h| &h[..4] == b"PK\x01\x02");
        let Some(header) = header else {
            return Err(DomainError::value("Archive ZIP directory member is invalid"));
        };
        actual += 1;
        if actual > maximum_members {
            return Err(DomainError::value("Archive has too many members"));
        }
        let lengths = u16_at(header, 28) + u16_at(header, 30) + u16_at(header, 32);
        let next = position as u64 + 46 + lengths;
        if next > end as u64 {
            return Err(DomainError::value("Archive ZIP directory member is truncated"));
        }
        position = next as usize;
    }
    if actual != count {
        return Err(DomainError::value("Archive ZIP directory count does not match"));
    }
    Ok(())
}
