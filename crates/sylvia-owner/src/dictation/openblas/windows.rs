//! The OpenBLAS that NumPy ships (scipy-openblas 0.3.34, `libscipy_openblas64_`), for the one
//! NumPy step of faster-whisper's features that runs in BLAS: `mel_filters @ magnitudes`.
//!
//! Rule 9 module (approved by the rewrite manager, 22 Nov 2026), Windows only, behind the
//! `dictation` feature. Calls:
//! - `LoadLibraryExW` with the library's full path and `LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR |
//!   LOAD_LIBRARY_SEARCH_SYSTEM32`; the library stays loaded for the life of the process.
//! - `GetProcAddress` for `scipy_openblas_get_config64_` (the version gate: "OpenBLAS 0.3.34"),
//!   `scipy_cblas_sgemm64_` and `scipy_cblas_sgemv64_`, each cast to its C prototype beside its
//!   lookup.
//!
//! OpenBLAS picks its thread count when it loads, from `OPENBLAS_NUM_THREADS`, then
//! `GOTO_NUM_THREADS`, then `OMP_NUM_THREADS`, as it does under NumPy. The desktop starts the
//! speech worker with `OMP_NUM_THREADS=2`; nothing here sets the count, because Python never
//! does. The thread partition decides which kernel path computes each value, so the bits follow
//! the variable exactly as in Python.
#![allow(unsafe_code)]

use std::ffi::{c_char, CStr, OsStr};
use std::os::windows::ffi::OsStrExt;
use std::path::Path;
use windows_sys::Win32::System::LibraryLoader::{
    GetProcAddress, LoadLibraryExW, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR, LOAD_LIBRARY_SEARCH_SYSTEM32,
};

/// The library's file name as NumPy 2.5.4's wheel ships it.
pub const LIBRARY: &str = "libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll";
const VERSION: &str = "OpenBLAS 0.3.34";

const CBLAS_ROW_MAJOR: i32 = 101;
const CBLAS_COL_MAJOR: i32 = 102;
const CBLAS_NO_TRANS: i32 = 111;
const CBLAS_TRANS: i32 = 112;

type GetConfig = unsafe extern "C" fn() -> *const c_char;
type Sgemm = unsafe extern "C" fn(
    i32,
    i32,
    i32,
    i64,
    i64,
    i64,
    f32,
    *const f32,
    i64,
    *const f32,
    i64,
    f32,
    *mut f32,
    i64,
);
type Sgemv =
    unsafe extern "C" fn(i32, i32, i64, i64, f32, *const f32, i64, *const f32, i64, f32, *mut f32, i64);

/// Why the library could not be used.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LoadError {
    /// The file is missing or Windows refused to load it.
    Library,
    /// A required export is missing.
    Symbol(&'static str),
    /// The library is not OpenBLAS 0.3.34.
    Version(String),
}

/// The loaded library.
pub struct OpenBlas {
    sgemm: Sgemm,
    sgemv: Sgemv,
    config: String,
}

fn wide(path: &Path) -> Vec<u16> {
    OsStr::new(path).encode_wide().chain(std::iter::once(0)).collect()
}

impl OpenBlas {
    /// Load `LIBRARY` from `folder` (the owner's own folder in the product).
    pub fn load(folder: &Path) -> Result<Self, LoadError> {
        // The search flags need a full path: resolve a relative folder first.
        let folder = std::path::absolute(folder).map_err(|_| LoadError::Library)?;
        let path = wide(&folder.join(LIBRARY));
        // SAFETY: `path` is a NUL-terminated UTF-16 full path that outlives the call; the
        // flags only restrict where dependencies are searched. The module is never freed, so
        // the function pointers below stay valid for the process.
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
        // SAFETY: the module is loaded and the name is a NUL-terminated ASCII export name.
        let found = unsafe { GetProcAddress(module, c"scipy_openblas_get_config64_".as_ptr().cast()) }
            .ok_or(LoadError::Symbol("scipy_openblas_get_config64_"))?;
        // SAFETY: the export is OpenBLAS's `char* openblas_get_config(void)` (with scipy's symbol
        // suffix), a C function taking nothing and returning a pointer to a static string.
        let get_config =
            unsafe { std::mem::transmute::<unsafe extern "system" fn() -> isize, GetConfig>(found) };
        // SAFETY: the function takes no arguments and returns a pointer to a static,
        // NUL-terminated string owned by the library, valid while it stays loaded (always).
        let config = unsafe { CStr::from_ptr(get_config()) }.to_string_lossy().into_owned();
        let after = config.strip_prefix(VERSION);
        if !after.is_some_and(|rest| !rest.starts_with(|c: char| c.is_ascii_digit())) {
            return Err(LoadError::Version(config));
        }
        // SAFETY: the module is loaded and the name is a NUL-terminated ASCII export name.
        let found = unsafe { GetProcAddress(module, c"scipy_cblas_sgemm64_".as_ptr().cast()) }
            .ok_or(LoadError::Symbol("scipy_cblas_sgemm64_"))?;
        // SAFETY: the export is the ILP64 `void cblas_sgemm(enum CBLAS_ORDER Order, enum
        // CBLAS_TRANSPOSE TransA, enum CBLAS_TRANSPOSE TransB, blasint M, blasint N, blasint K,
        // float alpha, const float *A, blasint lda, const float *B, blasint ldb, float beta,
        // float *C, blasint ldc)` with `blasint` = `int64_t` and the enums as `int`; `Sgemm` is
        // that prototype.
        let sgemm = unsafe { std::mem::transmute::<unsafe extern "system" fn() -> isize, Sgemm>(found) };
        // SAFETY: as above.
        let found = unsafe { GetProcAddress(module, c"scipy_cblas_sgemv64_".as_ptr().cast()) }
            .ok_or(LoadError::Symbol("scipy_cblas_sgemv64_"))?;
        // SAFETY: the export is the ILP64 `void cblas_sgemv(enum CBLAS_ORDER order, enum
        // CBLAS_TRANSPOSE trans, blasint m, blasint n, float alpha, const float *a, blasint lda,
        // const float *x, blasint incx, float beta, float *y, blasint incy)`; `Sgemv` is that
        // prototype.
        let sgemv = unsafe { std::mem::transmute::<unsafe extern "system" fn() -> isize, Sgemv>(found) };
        Ok(Self { sgemm, sgemv, config })
    }

    /// `openblas_get_config()`: the version and build line.
    pub fn config(&self) -> &str {
        &self.config
    }

    /// `filters @ magnitudes` as NumPy 2.5.4's `matmul` calls BLAS for faster-whisper's
    /// arrays: `filters` is C-ordered `rows × inner`, `magnitudes` Fortran-ordered `inner ×
    /// frames` (stored frame by frame), the result C-ordered `rows × frames`. One frame takes
    /// NumPy's matrix-vector case (`sgemv`), more take `sgemm`.
    pub fn product(
        &self,
        filters: &[f32],
        rows: usize,
        inner: usize,
        magnitudes: &[f32],
        frames: usize,
    ) -> Vec<f32> {
        assert_eq!(filters.len(), rows * inner, "filter bank shape");
        assert_eq!(magnitudes.len(), inner * frames, "magnitude shape");
        let mut out = vec![0.0_f32; rows * frames];
        let (rows_i, inner_i, frames_i) = (rows as i64, inner as i64, frames as i64);
        if frames == 0 || rows == 0 || inner == 0 {
            // NumPy's `any_zero_dim` case runs its own loop: every sum is empty, so zero.
            return out;
        }
        if frames == 1 {
            // SAFETY: column-major `inner × rows` with lda = inner is exactly `filters` read as
            // its transpose (`filters.len() == rows * inner`); `x` has `inner` elements with
            // stride 1 and `y` has `rows` writable elements with stride 1. Both buffers live
            // through the synchronous call and do not overlap.
            unsafe {
                (self.sgemv)(
                    CBLAS_COL_MAJOR,
                    CBLAS_TRANS,
                    inner_i,
                    rows_i,
                    1.0,
                    filters.as_ptr(),
                    inner_i,
                    magnitudes.as_ptr(),
                    1,
                    0.0,
                    out.as_mut_ptr(),
                    1,
                );
            }
        } else {
            // SAFETY: A is `rows × inner` row-major with lda = inner; B is read transposed from
            // `frames × inner` row-major storage with ldb = inner; C is `rows × frames` with ldc =
            // frames. The lengths are checked above, the buffers do not overlap and live through
            // the synchronous call.
            unsafe {
                (self.sgemm)(
                    CBLAS_ROW_MAJOR,
                    CBLAS_NO_TRANS,
                    CBLAS_TRANS,
                    rows_i,
                    frames_i,
                    inner_i,
                    1.0,
                    filters.as_ptr(),
                    inner_i,
                    magnitudes.as_ptr(),
                    inner_i,
                    0.0,
                    out.as_mut_ptr(),
                    frames_i,
                );
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_library_is_refused() {
        let folder = std::env::temp_dir().join("sylvia-no-openblas-here");
        assert_eq!(OpenBlas::load(&folder).err(), Some(LoadError::Library));
    }
}
