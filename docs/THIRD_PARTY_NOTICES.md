# Third-party notices — Windows v1.0.0 candidate

This document describes the current **Windows x86-64, folder-based candidate**. Only the FPVSIM-Clipper application code is covered by the root MIT License; third-party components retain their own terms. Recheck dependencies and notices whenever the build environment or package contents change.

| Component | Version in candidate | Terms and notices |
| --- | --- | --- |
| Python | 3.14.10 | PSF and other incorporated licenses: `LICENSES/Python-License.txt`, `LICENSES/Python-3.14-third-party-acknowledgements.html` |
| Qt Core, Gui, Network, Widgets, SVG and Qt plugins | 6.10.3 | LGPL-3.0 and other applicable terms; `LICENSES/LGPL-3.0.txt`, `LICENSES/GPL-3.0.txt`, and `LICENSES/qt-upstream-6.10.3` |
| PySide6 Essentials / Shiboken6 | 6.10.3 | LGPL-3.0 and other applicable terms; `LICENSES/qt-upstream-6.10.3/pyside-setup-everywhere-src-6.10.3` |
| websocket-client | 1.9.0 | Apache-2.0: `LICENSES/websocket-client-Apache-2.0.txt` |
| PyInstaller bootloader | 6.21.0 | GPL-2.0-or-later with a bootloader exception: `LICENSES/PyInstaller-COPYING.txt` |
| PyWinRT runtime and Windows projections | 3.2.1 | MIT: `LICENSES/PyWinRT-MIT.txt` |
| typing_extensions | 4.16.0 | PSF-2.0: `LICENSES/typing_extensions-LICENSE.txt` |
| OpenSSL | 3.5.9 | Apache-2.0: `LICENSES/OpenSSL-3.5.9.txt` |
| SQLite | 3.50.4 | Public-domain dedication: `LICENSES/SQLite-copyright.html` |
| libffi | 3.4.4, according to the CPython Windows build definition | MIT-style terms: `LICENSES/libffi-3.4.4.txt` |
| zstd | 1.5.7, according to the CPython Windows build definition | BSD-3-Clause: `LICENSES/zstd-1.5.7.txt` |
| zlib-ng | 2.2.4, according to the CPython Windows build definition | zlib license: `LICENSES/zlib-ng-2.2.4.md` |
| XZ / liblzma | 5.2.5, according to the CPython Windows build definition | `LICENSES/XZ-5.2.5.txt` |
| Microsoft runtime DLLs | 14.51.36247.0 | Microsoft Distributable Code; see `MICROSOFT_REDISTRIBUTABLE_NOTICE.md`, Windows binary conditions in `LICENSES/Python-License.txt`, and https://learn.microsoft.com/en-us/visualstudio/releases/2026/redistribution |

For this candidate, the applicable open-source option for Qt / PySide6 is LGPL-3.0-only. Qt and Qt for Python incorporate third-party code. Upstream attribution JSON and license/copyright files collected from the matching source releases are retained under `LICENSES/qt-upstream-6.10.3`; these include more files than the subset necessarily present in the binary, including texts for alternate license options. Inclusion of an alternate license text does not mean this candidate is distributed under a commercial Qt license. Python's acknowledgements also describe included components such as expat, zlib and libmpdec. These notices do not by themselves establish that every third-party component of the actual binary has been identified.

The listed Python dependency versions are based on the CPython 3.14.10 Windows build definition: https://github.com/python/cpython/blob/v3.14.10/PCbuild/get_externals.bat. The Python binary is the official Windows x64 embedded distribution, SHA-256 `90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159`.

No FFmpeg executable or library is intentionally bundled. The optional Qt Mesa software OpenGL renderer (`opengl32sw.dll`) and Qt translation catalogs are excluded; the application does not install a Qt translator. Windows media processing uses Windows Runtime APIs. macOS is not supported in this release. License files for unused components may also be included for reference.

## Source and replacement

The release preparer has obtained these upstream source archives. Their SHA-256 values match those published on the respective Qt download mirror-list pages. Exact correspondence to every distributed binary, long-term distribution availability and any downstream patches/build instructions must still be verified before release.

| Component | Upstream source | SHA-256 |
| --- | --- | --- |
| PySide6 / Shiboken 6.10.3 | https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.3-src/pyside-setup-everywhere-src-6.10.3.tar.xz | `2c7462fe0cecb5b8ac0a3d92014b8d0b88bd4d9f8646709dab5286d9416f45bc` |
| Qt Base 6.10.3 | https://download.qt.io/archive/qt/6.10/6.10.3/submodules/qtbase-everywhere-src-6.10.3.tar.xz | `383dc907816338f0cba72088a524c07458dfc69ce684ca9132fcc4fe91c24b0b` |
| Qt SVG 6.10.3 | https://download.qt.io/archive/qt/6.10/6.10.3/submodules/qtsvg-everywhere-src-6.10.3.tar.xz | `b3223fe005f6a4c7f21f34e4ee6ce0094737cff9503ba15bbb171ac18794f76d` |
| Qt Image Formats 6.10.3 | https://download.qt.io/archive/qt/6.10/6.10.3/submodules/qtimageformats-everywhere-src-6.10.3.tar.xz | `84605dd91037482b5b7c7ecc5c27aee8acc1cd7f1fe77bc564777ddf365d7d28` |

Qt for Python source build instructions: https://doc.qt.io/qtforpython-6.10/building_from_source/index.html. Qt licensing: https://doc.qt.io/qt-6/licensing.html. Library-replacement observations and limitations are in `QT_REPLACEMENT.md`.

The release preparer must confirm all terms and notices for the exact files shipped. This document is technical preparation, not legal advice or a determination of compliance.
