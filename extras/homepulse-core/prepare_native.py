"""Prepare a private compatibility COPY from the user's Oura 7.10.0 APK/APKM.

No target code is executed. Only ELF dynamic linker metadata is adapted;
.text/.rodata must remain byte-identical. No native binary belongs in Git.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import zipfile

ORIGINAL_SHA256 = 'a4e8a213f87283cae183f70ab0ed17d0e30aa259868d757a1385189d1a43ca08'
ORIGINAL_BYTES = 2_251_632
MEMBER = 'lib/x86_64/libappecore.so'


def extract_library(source):
    path = Path(source)
    if path.is_symlink() or not path.is_file():
        raise ValueError('APK/APKM originale non disponibile o symlink')
    if path.suffix == '.so':
        if path.stat().st_size != ORIGINAL_BYTES:
            raise ValueError('Dimensione libreria originale inattesa')
        return path.read_bytes()
    with zipfile.ZipFile(path) as outer:
        if MEMBER in outer.namelist():
            info = outer.getinfo(MEMBER)
            if info.file_size != ORIGINAL_BYTES:
                raise ValueError('Dimensione membro nativo inattesa')
            return outer.read(info)
        for info in outer.infolist():
            if info.filename.endswith('.apk') and ('x86_64' in info.filename or info.filename == 'base.apk'):
                if info.file_size > 256_000_000:
                    raise ValueError('Split APK oltre il limite di preparazione')
                with zipfile.ZipFile(io.BytesIO(outer.read(info))) as inner:
                    if MEMBER in inner.namelist():
                        member = inner.getinfo(MEMBER)
                        if member.file_size != ORIGINAL_BYTES:
                            raise ValueError('Dimensione membro nativo inattesa')
                        return inner.read(member)
    raise ValueError('Libreria x86_64 assente dal pacchetto fornito')


def _sections(raw):
    offset = struct.unpack_from('<Q', raw, 40)[0]
    width, count, strings_index = struct.unpack_from('<HHH', raw, 58)
    sections = [struct.unpack_from('<IIQQQQIIQQ', raw, offset + i * width) for i in range(count)]
    strings = sections[strings_index]
    names = raw[strings[4]:strings[4] + strings[5]]
    result = {}
    for section in sections:
        name = names[section[0]:names.index(0, section[0])].decode()
        if name in ('.text', '.rodata'):
            result[name] = raw[section[4]:section[4] + section[5]]
    return result


def patch_library(raw):
    if hashlib.sha256(raw).hexdigest() != ORIGINAL_SHA256:
        raise ValueError('Hash libreria originale diverso dalla versione verificata')
    if raw[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', raw, 18)[0] != 62:
        raise ValueError('Richiesto ELF64 little-endian x86_64')
    patched = bytearray(raw)
    phoff = struct.unpack_from('<Q', raw, 32)[0]
    width, count = struct.unpack_from('<HH', raw, 54)
    headers = [struct.unpack_from('<IIQQQQQQ', raw, phoff + i * width) for i in range(count)]
    dynamic = next(p for p in headers if p[0] == 2)

    def file_offset(address):
        for p in headers:
            if p[0] == 1 and p[3] <= address < p[3] + p[5]:
                return p[2] + address - p[3]
        raise ValueError('Indirizzo ELF non mappato')

    entries = []
    for offset in range(dynamic[2], dynamic[2] + dynamic[5], 16):
        tag, value = struct.unpack_from('<qQ', raw, offset)
        if tag == 0:
            break
        entries.append((offset, tag, value))
    strings = file_offset(next(v for o, t, v in entries if t == 5))
    dependencies = []
    for offset, tag, value in entries:
        if tag == 1:
            start = strings + value
            end = raw.index(0, start)
            name = raw[start:end].decode()
            if name not in {'liblog.so', 'libm.so', 'libdl.so', 'libc.so'}:
                raise ValueError('Dipendenza ELF non prevista')
            patched[start:end + 1] = b'libx.so' + bytes(end - start + 1 - 7)
            dependencies.append(name)
        if tag in (0x6ffffff0, 0x6ffffffe, 0x6fffffff):
            struct.pack_into('<qQ', patched, offset, 21, 0)  # DT_DEBUG, ignored for this shared object.
    if set(dependencies) != {'liblog.so', 'libm.so', 'libdl.so', 'libc.so'}:
        raise ValueError('Insieme dipendenze diverso dalla versione verificata')
    sections = _sections(raw)
    if set(sections) != {'.text', '.rodata'} or sections != _sections(patched):
        raise ValueError('Le sezioni algoritmo devono rimanere identiche')
    result = bytes(patched)
    manifest = {'original_sha256': ORIGINAL_SHA256, 'compat_sha256': hashlib.sha256(result).hexdigest(),
                'architecture': 'x86_64', 'native_version_expected': [3, 6, 2],
                'changed_byte_count': sum(a != b for a, b in zip(raw, result)),
                'algorithm_sections': {name: {'unchanged': True, 'sha256': hashlib.sha256(value).hexdigest()} for name, value in sections.items()},
                'limitations': ['Minimal Bionic shim; FILE/stdio not ported', 'Not clinical validation or bit-exact Android equivalence']}
    return result, manifest


def prepare(source, destination):
    raw, manifest = patch_library(extract_library(source))
    folder = Path(destination).absolute()
    if folder.is_symlink():
        raise ValueError('La cartella privata non può essere un symlink')
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != 'nt' and (stat.S_IMODE(folder.stat().st_mode) != 0o700 or folder.stat().st_uid != os.getuid()):
        raise ValueError('La cartella privata deve appartenere all’utente con permessi0700')
    for name, payload in [('libappecore-compat.so', raw), ('manifest.json', json.dumps(manifest, indent=2).encode())]:
        descriptor = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Il proprio APK/APKM, oppure ELF originale già estratto')
    parser.add_argument('--out', required=True, help='Nuova cartella privata; non usare un percorso in Git')
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.out), indent=2))


if __name__ == '__main__':
    main()
