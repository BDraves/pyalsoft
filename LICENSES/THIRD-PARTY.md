# Third-party notices

PyALSoft includes and generates code from third-party projects. Those portions
remain under their respective licenses; the MIT License for PyALSoft's original
code does not replace them.

## OpenAL Soft

Platform wheels bundle an unmodified OpenAL Soft 1.25.2 shared library. OpenAL
Soft is licensed under the GNU Library General Public License version 2 or, at
your option, any later version (`LGPL-2.0-or-later`). The applicable license is
in `vendor/openal-soft/COPYING`.

The exact corresponding source archive is distributed at
`vendor/openal-soft/openal-soft-1.25.2.tar.bz2`. Its upstream project is
<https://github.com/kcat/openal-soft>.

OpenAL Soft includes a modified PFFFT implementation under a permissive
three-clause license. Its notice is in `vendor/openal-soft/LICENSE-pffft`.

## OpenAL API Registry

PyALSoft's generated bindings are derived from the OpenAL API Registry. The
registry's copyright and permission notice is in
`LICENSES/OpenAL-Registry.txt`. That notice states that use of the registry
alone is unencumbered by the OpenAL Soft LGPL terms.

## miniaudio

Platform wheels include a decoder-only helper built from miniaudio 0.11.25.
PyALSoft uses its WAV, FLAC, and MP3 decoding components without its playback
device layer. The upstream project is <https://github.com/mackron/miniaudio>.
Its MIT-0 license is reproduced in `LICENSES/miniaudio.txt` and in the vendored
source.

## stb_vorbis

The decoder helper uses stb_vorbis from the pinned stb commit recorded in
`vendor/audio-decoder/source.toml`. The upstream project is
<https://github.com/nothings/stb>. Its dual MIT/public-domain notice is
reproduced in `LICENSES/stb_vorbis.txt` and in the vendored source.

## {fmt}

OpenAL Soft includes {fmt}. Its MIT license and compiled-object exception are
in `LICENSES/fmt.txt`.

## Microsoft Guidelines Support Library

OpenAL Soft includes headers from the Microsoft Guidelines Support Library.
Its MIT license is in `LICENSES/Microsoft-GSL.txt`.

## Trademarks

OpenAL is a trademark of Creative Labs, Inc. PyALSoft is an independent project
and is not affiliated with or endorsed by Creative Labs or the OpenAL Soft
project.
