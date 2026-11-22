//! libswresample from the LGPL FFmpeg 8.1.2 build the product ships for PyAV, for
//! faster-whisper's resampling to 16-bit mono 16 kHz.
//!
//! Rule 9 module (approved by the rewrite manager, 22 Nov 2026), Windows only, behind the
//! `dictation` feature. Calls:
//! - `LoadLibraryExW` with `swresample-6.dll`'s full path and `LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR |
//!   LOAD_LIBRARY_SEARCH_SYSTEM32` (so its `avutil-60.dll` resolves beside it); never freed.
//! - `GetProcAddress` for `swresample_version` (the gate: major version 6), `swr_alloc_set_opts2`,
//!   `swr_init`, `swr_get_out_samples`, `swr_convert` and `swr_free`, each cast to its C
//!   prototype beside its lookup.
//! - One `SwrContext` per resampling, owned by [`Resampler`] and freed with `swr_free` on drop.
//!
//! PyAV's `AudioResampler(format="s16", layout="mono", rate=16000)` builds a filter graph in
//! which FFmpeg inserts `aresample` with default options; this is the same libswresample with
//! the same (default) options, fed the same groups and flushed the same way.
#![allow(unsafe_code)]

use std::ffi::{c_int, c_uint, c_void, OsStr};
use std::os::windows::ffi::OsStrExt;
use std::path::Path;
use windows_sys::Win32::System::LibraryLoader::{
    GetProcAddress, LoadLibraryExW, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR, LOAD_LIBRARY_SEARCH_SYSTEM32,
};

/// The library's file name in the shipped LGPL build.
pub const LIBRARY: &str = "swresample-6.dll";

/// `AV_SAMPLE_FMT_S16`.
const AV_SAMPLE_FMT_S16: c_int = 1;
/// `AV_CHANNEL_ORDER_NATIVE`.
const AV_CHANNEL_ORDER_NATIVE: c_int = 1;
/// `AV_CH_FRONT_CENTER`.
const AV_CH_FRONT_CENTER: u64 = 0x4;

/// FFmpeg 8's `AVChannelLayout`: `enum AVChannelOrder order; int nb_channels; union { uint64_t
/// mask; AVChannelCustom *map; } u; void *opaque;`.
#[repr(C)]
struct AVChannelLayout {
    order: c_int,
    nb_channels: c_int,
    /// The union, as its `mask` member (8 bytes, 8-aligned, the same as the pointer member).
    mask: u64,
    opaque: *mut c_void,
}

const _: () = assert!(std::mem::size_of::<AVChannelLayout>() == 24);
const _: () = assert!(std::mem::align_of::<AVChannelLayout>() == 8);

impl AVChannelLayout {
    /// `AV_CHANNEL_LAYOUT_MONO`.
    fn mono() -> Self {
        Self {
            order: AV_CHANNEL_ORDER_NATIVE,
            nb_channels: 1,
            mask: AV_CH_FRONT_CENTER,
            opaque: std::ptr::null_mut(),
        }
    }
}

type Version = unsafe extern "C" fn() -> c_uint;
type AllocSetOpts2 = unsafe extern "C" fn(
    *mut *mut c_void,
    *const AVChannelLayout,
    c_int,
    c_int,
    *const AVChannelLayout,
    c_int,
    c_int,
    c_int,
    *mut c_void,
) -> c_int;
type Init = unsafe extern "C" fn(*mut c_void) -> c_int;
type GetOutSamples = unsafe extern "C" fn(*mut c_void, c_int) -> c_int;
type Convert = unsafe extern "C" fn(*mut c_void, *const *mut u8, c_int, *const *const u8, c_int) -> c_int;
type Free = unsafe extern "C" fn(*mut *mut c_void);

/// Why libswresample could not be used.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LoadError {
    /// The file (or `avutil-60.dll` beside it) is missing, or Windows refused it.
    Library,
    /// A required export is missing.
    Symbol(&'static str),
    /// `swresample_version()` is not 6.x.
    Version(u32),
}

/// A libswresample call returned an error code.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SwrError(pub i32);

/// The loaded library.
pub struct Swresample {
    version: u32,
    alloc_set_opts2: AllocSetOpts2,
    init: Init,
    get_out_samples: GetOutSamples,
    convert: Convert,
    free: Free,
}

fn wide(path: &Path) -> Vec<u16> {
    OsStr::new(path).encode_wide().chain(std::iter::once(0)).collect()
}

impl Swresample {
    /// Load `LIBRARY` from `folder` (the owner's own folder in the product).
    pub fn load(folder: &Path) -> Result<Self, LoadError> {
        // The search flags need a full path: resolve a relative folder first.
        let folder = std::path::absolute(folder).map_err(|_| LoadError::Library)?;
        let path = wide(&folder.join(LIBRARY));
        // SAFETY: `path` is a NUL-terminated UTF-16 full path that outlives the call; the flags
        // only restrict where dependencies are searched. The module is never freed, so the
        // function pointers below stay valid for the process.
        let module = unsafe {
            LoadLibraryExW(
                path.as_ptr(),
                std::ptr::null_mut(),
                LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32,
            )
        };
        if module.is_null() {
            return Err(LoadError::Library);
        }
        // SAFETY (each lookup): the module is loaded and the name is a NUL-terminated ASCII
        // export name.
        let lookup = |name: &'static std::ffi::CStr, label: &'static str| {
            unsafe { GetProcAddress(module, name.as_ptr().cast()) }.ok_or(LoadError::Symbol(label))
        };
        let found = lookup(c"swresample_version", "swresample_version")?;
        // SAFETY: `unsigned swresample_version(void);`
        let version_fn =
            unsafe { std::mem::transmute::<unsafe extern "system" fn() -> isize, Version>(found) };
        // SAFETY: takes nothing and returns `LIBSWRESAMPLE_VERSION_INT`.
        let version = unsafe { version_fn() };
        if version >> 16 != 6 {
            return Err(LoadError::Version(version));
        }
        let found = lookup(c"swr_alloc_set_opts2", "swr_alloc_set_opts2")?;
        // SAFETY: `int swr_alloc_set_opts2(struct SwrContext **ps, const AVChannelLayout
        // *out_ch_layout, enum AVSampleFormat out_sample_fmt, int out_sample_rate, const
        // AVChannelLayout *in_ch_layout, enum AVSampleFormat in_sample_fmt, int in_sample_rate,
        // int log_offset, void *log_ctx);`
        let alloc_set_opts2 =
            unsafe { std::mem::transmute::<unsafe extern "system" fn() -> isize, AllocSetOpts2>(found) };
        let found = lookup(c"swr_init", "swr_init")?;
        // SAFETY: `int swr_init(struct SwrContext *s);`
        let init = unsafe { std::mem::transmute::<unsafe extern "system" fn() -> isize, Init>(found) };
        let found = lookup(c"swr_get_out_samples", "swr_get_out_samples")?;
        // SAFETY: `int swr_get_out_samples(struct SwrContext *s, int in_samples);`
        let get_out_samples =
            unsafe { std::mem::transmute::<unsafe extern "system" fn() -> isize, GetOutSamples>(found) };
        let found = lookup(c"swr_convert", "swr_convert")?;
        // SAFETY: `int swr_convert(struct SwrContext *s, uint8_t * const *out, int out_count,
        // const uint8_t * const *in, int in_count);`
        let convert = unsafe { std::mem::transmute::<unsafe extern "system" fn() -> isize, Convert>(found) };
        let found = lookup(c"swr_free", "swr_free")?;
        // SAFETY: `void swr_free(struct SwrContext **s);`
        let free = unsafe { std::mem::transmute::<unsafe extern "system" fn() -> isize, Free>(found) };
        Ok(Self { version, alloc_set_opts2, init, get_out_samples, convert, free })
    }

    /// `LIBSWRESAMPLE_VERSION_INT` of the loaded library.
    pub fn version(&self) -> u32 {
        self.version
    }

    /// A resampler from 16-bit mono at `rate` to 16-bit mono 16 kHz, default options.
    pub fn mono_s16_to_16k(&self, rate: i32) -> Result<Resampler<'_>, SwrError> {
        self.open(&AVChannelLayout::mono(), rate)
    }

    fn open(&self, input: &AVChannelLayout, rate: i32) -> Result<Resampler<'_>, SwrError> {
        let output = AVChannelLayout::mono();
        let mut context: *mut c_void = std::ptr::null_mut();
        // SAFETY: `context` is a writable out-pointer; both layouts are valid `AVChannelLayout`s
        // read during the call only; no log context is passed.
        let code = unsafe {
            (self.alloc_set_opts2)(
                &mut context,
                &output,
                AV_SAMPLE_FMT_S16,
                16_000,
                input,
                AV_SAMPLE_FMT_S16,
                rate,
                0,
                std::ptr::null_mut(),
            )
        };
        // From here the context, if any, is owned and freed by the resampler's drop.
        let resampler = Resampler { library: self, context };
        if code < 0 {
            return Err(SwrError(code));
        }
        // SAFETY: `context` was allocated by `swr_alloc_set_opts2` above and is not shared.
        let code = unsafe { (self.init)(resampler.context) };
        if code < 0 {
            return Err(SwrError(code));
        }
        Ok(resampler)
    }
}

/// One initialised `SwrContext`.
pub struct Resampler<'a> {
    library: &'a Swresample,
    context: *mut c_void,
}

impl Resampler<'_> {
    fn run(&mut self, input: Option<&[i16]>, capacity: c_int) -> Result<Vec<i16>, SwrError> {
        let capacity = capacity.max(0);
        let mut out = vec![0_i16; capacity as usize];
        let outs = [out.as_mut_ptr().cast::<u8>()];
        let (ins, count) = match input {
            Some(samples) => {
                ([samples.as_ptr().cast::<u8>()], c_int::try_from(samples.len()).map_err(|_| SwrError(-22))?)
            }
            None => ([std::ptr::null::<u8>()], 0),
        };
        let in_ptr = if input.is_some() { ins.as_ptr() } else { std::ptr::null() };
        // SAFETY: the context is initialised and owned; `outs` holds one plane of `capacity`
        // writable samples (packed mono s16); `ins` holds one plane of `count` readable samples,
        // or the input is NULL with count 0 to flush. All buffers outlive the synchronous call.
        let written = unsafe { (self.library.convert)(self.context, outs.as_ptr(), capacity, in_ptr, count) };
        if written < 0 {
            return Err(SwrError(written));
        }
        out.truncate(written as usize);
        Ok(out)
    }

    /// `swr_convert` of one group of input samples, with room for every sample it can return.
    pub fn convert(&mut self, input: &[i16]) -> Result<Vec<i16>, SwrError> {
        let count = c_int::try_from(input.len()).map_err(|_| SwrError(-22))?;
        // SAFETY: the context is initialised and owned.
        let capacity = unsafe { (self.library.get_out_samples)(self.context, count) };
        if capacity < 0 {
            return Err(SwrError(capacity));
        }
        self.run(Some(input), capacity)
    }

    /// Flush: `swr_convert` with no input until it returns nothing (`aresample` at end of
    /// stream, 4096 samples a call).
    pub fn flush(&mut self) -> Result<Vec<i16>, SwrError> {
        let mut out = Vec::new();
        loop {
            let chunk = self.run(None, 4096)?;
            if chunk.is_empty() {
                return Ok(out);
            }
            out.extend(chunk);
        }
    }
}

impl Drop for Resampler<'_> {
    fn drop(&mut self) {
        if !self.context.is_null() {
            // SAFETY: the context came from `swr_alloc_set_opts2`, is owned here alone, and
            // `swr_free` sets the pointer to NULL.
            unsafe { (self.library.free)(&mut self.context) };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_library_is_refused() {
        let folder = std::env::temp_dir().join("sylvia-no-swresample-here");
        assert!(matches!(Swresample::load(&folder), Err(LoadError::Library)));
    }

    /// Needs `SYLVIA_DICTATION_NATIVE_DIR` (see the dictation check in docs/rust-port/README.md).
    #[test]
    #[ignore = "needs SYLVIA_DICTATION_NATIVE_DIR with the shipped LGPL FFmpeg DLLs"]
    fn the_mono_layout_initialises_and_a_wrong_channel_count_is_refused() {
        let folder = std::env::var_os("SYLVIA_DICTATION_NATIVE_DIR").expect("SYLVIA_DICTATION_NATIVE_DIR");
        let library = Swresample::load(Path::new(&folder)).expect("libswresample 6 loads");
        assert_eq!(library.version() >> 16, 6);
        assert!(library.mono_s16_to_16k(48_000).is_ok());
        // Two channels with a one-channel mask is not a valid native layout.
        let wrong = AVChannelLayout { nb_channels: 2, ..AVChannelLayout::mono() };
        assert!(library.open(&wrong, 48_000).is_err());
    }
}
