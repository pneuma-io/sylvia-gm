# FFmpeg source and recombination

The Windows release package uses seven shared FFmpeg 8.1.2 libraries under
LGPL-2.1-or-later and the BSD PyAV 18.0.0 wrapper. Original vendor x264/x265 and
other av.libs DLLs are excluded. Original first-party and third-party notices
remain separate and unchanged; this file does not replace their terms.

The matching release provides `DICTATION-CODEC-SOURCE.zip` beside its binary
downloads, together with the application source archive:

The matching DICTATION-CODEC-SOURCE.zip accompanies this private candidate.
The codec bytes are identical to the supplement already available at:
https://github.com/pneuma-io/sylvia-gm/releases/tag/v0.3.0-alpha.2

The codec supplement contains the exact original FFmpeg source archive, PyAV
source, original BSD wrapper objects without vendor DLLs, the build scripts and
build-only patches. Its inventory identifies every supplied byte. The original
FFmpeg source is also available at:

https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz

SHA256: `464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c`.

To recombine a modified, ABI-compatible FFmpeg build with Sylvia:

1. Extract the matching application and codec source archives into new working
   directories. Keep existing installations and personal data separate.
2. Follow the codec supplement's Windows build recipe. The selected configuration
   and original/modified build scripts are supplied; no FFmpeg C source was
   changed. The release used MSVC 19.50.35725 x64, GNU Make 3.82.90 and Git Bash.
3. Keep the seven DLL basenames and their existing public ABI. Run the supplied
   PyAV staging recipe against them. The staging changes only PE import names,
   checksums and the DLL search bootstrap; compiled extension code is unchanged.
4. For a personal modified build, update the library hashes in that working copy's
   `licenses/dictation/PYAV-LGPL-BUILD.json` to identify the chosen DLLs. The
   official manifest deliberately binds the official release's inputs. It is a
   build integrity check, not a runtime signature or a restriction on replacing
   the LGPL libraries.
5. Set `SYLVIA_FFMPEG_LGPL_DIR` to the rebuilt DLL directory and run
   `uv run --locked --all-extras python src-tauri/scripts/build_owner_sidecar.py`.
   This creates a new owner executable containing the same application code and
   the selected replacement libraries. Use that owner in a separate matching
   application copy and verify its startup and audio behavior. Label the result
   as modified; the official binary checksums no longer apply.

The onefile owner extracts its bundled libraries when it starts. Editing an
already-running temporary extraction directory is not the supported replacement
route; rebuilding the owner from the supplied application code and binding
objects is the selected recombination route. The original application source,
notices and library source remain available for that work. Incompatible ABI or
dependency changes can prevent a modified build from loading.

See LGPL 2.1 sections 5 and 6 for the applicable terms, including library
modification and reverse engineering for debugging such modifications. No
additional first-party licence grant or general legal clearance is asserted.
