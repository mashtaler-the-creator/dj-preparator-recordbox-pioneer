#!/usr/bin/env python3
"""Generate the synthetic trap set covering every case from RESEARCH.md §1.
Each track gets acoustically distinct content (different sine pairs + tremolo rate)
so chromaprint fingerprints do NOT collide between different tracks; duplicate
pairs share the exact same master WAV."""

import struct
import subprocess
import sys
from pathlib import Path

from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TRCK
from mutagen.mp4 import MP4

FF = "/opt/homebrew/bin/ffmpeg"
HERE = Path(__file__).parent
SRC = HERE / "src"
ALBUM = SRC / "Test Artist - Trap Album (2024)"


def run(*cmd):
    r = subprocess.run([str(c) for c in cmd], stdin=subprocess.DEVNULL,
                       capture_output=True)
    if r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(map(str, cmd))}\n{r.stderr.decode()}")
    return r


def master(idx, path, seconds=25, rate=44100, extra_af=None, mono=False, bpm=None):
    """Distinct-per-index audio master: an 8-note pseudo-melody (per-index PRNG) over pink
    noise. Steady tones give degenerate chromaprints that false-match everything; a melody
    per track keeps fingerprints distinct like real music."""
    import random
    rng = random.Random(idx * 7919)
    base = 165.0 * 2 ** (rng.randrange(12) / 12)
    notes = [rng.randrange(0, 13) for _ in range(8)]
    freq_expr = f"{base * 2 ** (notes[-1] / 12):.2f}"
    for k in range(len(notes) - 2, -1, -1):
        f = base * 2 ** (notes[k] / 12)
        freq_expr = f"if(lt(mod(t\\,4)\\,{0.5 * (k + 1)})\\,{f:.2f}\\,{freq_expr})"
    trem = 1.5 + 0.37 * idx
    graph = (f"aevalsrc=0.6*sin(2*PI*{freq_expr}*t):d={seconds}:s={rate}[m];"
             f"anoisesrc=color=pink:seed={idx}:amplitude=0.22:duration={seconds}:sample_rate={rate}[n];"
             f"[m][n]amix=inputs=2:normalize=1,tremolo=f={trem}:d=0.7")
    if bpm:  # crude kick pattern for BPM detection
        period = 60.0 / bpm
        graph += (f",aeval=val(0)+0.6*sin(2*PI*55*t)*exp(-25*mod(t\\,{period}))"
                  f"*lt(mod(t\\,{period})\\,0.25)")
    graph += ",pan=mono|c0=c0" if mono else ",pan=stereo|c0=c0|c1=c0"
    af = graph
    if extra_af:
        af += "," + extra_af
    run(FF, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", af,
        "-c:a", "pcm_f32le", str(path))


def enc(src, dst, *args):
    run(FF, "-nostdin", "-v", "error", "-y", "-i", str(src), "-map_metadata", "-1",
        *args, str(dst))


def make_extensible(path):
    """Patch a plain 16-bit PCM WAV fmt chunk to WAVE_FORMAT_EXTENSIBLE (0xFFFE)."""
    data = bytearray(path.read_bytes())
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    pos = 12
    while pos < len(data):
        cid = bytes(data[pos:pos + 4])
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            body = data[pos + 8:pos + 8 + size]
            channels = struct.unpack("<H", body[2:4])[0]
            bits = struct.unpack("<H", body[14:16])[0]
            guid_pcm = bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x10, 0x00,
                              0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71])
            new_body = bytearray(body[:16])
            new_body[0:2] = struct.pack("<H", 0xFFFE)
            new_body += struct.pack("<H", 22)          # cbSize
            new_body += struct.pack("<H", bits)        # valid bits
            new_body += struct.pack("<I", 0x3 if channels == 2 else 0x4)
            new_body += guid_pcm
            new = data[:pos + 4] + struct.pack("<I", len(new_body)) + new_body \
                + data[pos + 8 + size + (size & 1):]
            new[4:8] = struct.pack("<I", len(new) - 8)
            path.write_bytes(bytes(new))
            return
        pos += 8 + size + (size & 1)
    sys.exit("fmt chunk not found")


def tag_flac(path, title, artist, album="Trap Album", track=None, art=None):
    f = FLAC(path)
    f["title"], f["artist"], f["album"], f["date"] = [title], [artist], [album], ["2024"]
    if track:
        f["tracknumber"] = [str(track)]
    if art:
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, "image/png", art
        f.add_picture(pic)
    f.save()


def tag_mp3(path, title, artist, album="Trap Album", track=None):
    t = ID3()
    t.add(TIT2(encoding=1, text=[title]))
    t.add(TPE1(encoding=1, text=[artist]))
    t.add(TALB(encoding=1, text=[album]))
    t.add(TDRC(encoding=0, text=["2024"]))
    if track:
        t.add(TRCK(encoding=0, text=[str(track)]))
    t.save(path)


def main():
    if SRC.exists():
        import shutil
        shutil.rmtree(SRC)
    ALBUM.mkdir(parents=True)
    tmp = HERE / "_masters"
    tmp.mkdir(exist_ok=True)

    def m(idx, **kw):
        p = tmp / f"m{idx}.wav"
        master(idx, p, **kw)
        return p

    # 01 FLAC 16/44.1 stereo + tags + oversized PNG art + 174 BPM pattern -> convert (PCM-identical)
    run(FF, "-nostdin", "-v", "error", "-y", "-f", "lavfi",
        "-i", "color=c=magenta:size=1000x1000:duration=0.04", "-frames:v", "1",
        str(tmp / "art.png"))
    p = m(1, bpm=174)
    enc(p, ALBUM / "01. FLAC16 Track.flac", "-ar", "44100", "-sample_fmt", "s16",
        "-c:a", "flac")
    tag_flac(ALBUM / "01. FLAC16 Track.flac", "FLAC16 Track", "Test Artist", track=1,
             art=(tmp / "art.png").read_bytes())

    # 02 FLAC 24/96 -> convert with resample + dither
    enc(m(2), ALBUM / "02. Hires FLAC Track.flac", "-ar", "96000", "-sample_fmt", "s32",
        "-bits_per_raw_sample", "24", "-c:a", "flac")
    tag_flac(ALBUM / "02. Hires FLAC Track.flac", "Hires FLAC Track", "Test Artist", track=2)

    # 03 ALAC -> convert
    enc(m(3), ALBUM / "03. ALAC Track.m4a", "-ar", "44100", "-c:a", "alac")
    f = MP4(ALBUM / "03. ALAC Track.m4a")
    f["\xa9nam"], f["\xa9ART"], f["\xa9alb"] = ["ALAC Track"], ["Test Artist"], ["Trap Album"]
    f.save()

    # 04 96 kHz 24-bit WAV -> convert
    enc(m(4), ALBUM / "04. WAV96 Track.wav", "-ar", "96000", "-c:a", "pcm_s24le")

    # 05 32-bit float WAV -> convert (dither)
    enc(m(5), ALBUM / "05. Float WAV Track.wav", "-ar", "44100", "-c:a", "pcm_f32le")

    # 06 WAVE_FORMAT_EXTENSIBLE 16/44.1 -> convert (header trap)
    enc(m(6), ALBUM / "06. Extensible Track.wav", "-ar", "44100", "-c:a", "pcm_s16le")
    make_extensible(ALBUM / "06. Extensible Track.wav")

    # 07 AIFF-C -> convert (ffmpeg writes AIFC for little-endian pcm in .aiff)
    enc(m(7), ALBUM / "07. AIFC Track.aiff", "-ar", "44100", "-c:a", "pcm_s16le")

    # 08 plain AIFF 16/44.1 big-endian -> copy
    enc(m(8), ALBUM / "08. Plain AIFF Track.aiff", "-ar", "44100", "-c:a", "pcm_s16be")

    # 09 MP3 320 CBR -> copy
    enc(m(9), ALBUM / "09. MP3 320 Track.mp3", "-ar", "44100", "-c:a", "libmp3lame",
        "-b:a", "320k")
    tag_mp3(ALBUM / "09. MP3 320 Track.mp3", "MP3 320 Track", "Test Artist", track=9)

    # 10 mono FLAC -> convert + mono warning
    enc(m(10, mono=True), ALBUM / "10. Mono Track.flac", "-ar", "44100",
        "-sample_fmt", "s16", "-c:a", "flac")
    tag_flac(ALBUM / "10. Mono Track.flac", "Mono Track", "Test Artist", track=10)

    # 11 DC offset -> convert + warning
    enc(m(11, extra_af="dcshift=0.05,volume=0.5"), ALBUM / "11. DC Offset Track.flac",
        "-ar", "44100", "-sample_fmt", "s16", "-c:a", "flac")
    tag_flac(ALBUM / "11. DC Offset Track.flac", "DC Offset Track", "Test Artist", track=11)

    # 12 clipped (true peak > 0) plain WAV within spec -> copy + clipping warning
    clip_expr = "min(max(2*sin(2*PI*997*t)\\,-1)\\,1)"
    run(FF, "-nostdin", "-v", "error", "-y", "-f", "lavfi",
        "-i", f"aevalsrc={clip_expr}|{clip_expr}:d=25:s=44100",
        "-c:a", "pcm_s16le", str(ALBUM / "12. Clipped Track.wav"))

    # 13 truncated FLAC -> reject
    full = tmp / "full13.flac"
    enc(m(13), full, "-ar", "44100", "-sample_fmt", "s16", "-c:a", "flac")
    data = full.read_bytes()
    (ALBUM / "13. Truncated Track.flac").write_bytes(data[: int(len(data) * 0.4)])

    # 14 no tags at all, release-structured path -> tags recovered from path
    enc(m(14), ALBUM / "14. Test Artist - Pathtag Track.flac", "-ar", "44100",
        "-sample_fmt", "s16", "-c:a", "flac")

    # orphan: no tags, no structure -> critical-tag warning
    enc(m(15), SRC / "orphan.flac", "-ar", "44100", "-sample_fmt", "s16", "-c:a", "flac")

    # Cyrillic filename + tags -> transliteration + ORIG tags
    cyr = SRC / "Кириллица - Альбом (2023)"
    cyr.mkdir()
    enc(m(16), cyr / "01. Кириллица - Трек 😎.flac", "-ar", "44100",
        "-sample_fmt", "s16", "-c:a", "flac")
    tag_flac(cyr / "01. Кириллица - Трек 😎.flac", "Трек 😎", "Кириллица",
             album="Альбом", track=1)

    # nesting deeper than the CDJ limit of 8 levels
    deep = SRC / "deep/l2/l3/l4/l5/l6/l7/l8/l9"
    deep.mkdir(parents=True)
    enc(m(17), deep / "Deep Track.flac", "-ar", "44100", "-sample_fmt", "s16",
        "-c:a", "flac")
    tag_flac(deep / "Deep Track.flac", "Deep Track", "Test Artist")

    # duplicate across two formats: same master -> FLAC + MP3
    dup = m(18)
    enc(dup, ALBUM / "18. Duplicate Track.flac", "-ar", "44100", "-sample_fmt", "s16",
        "-c:a", "flac")
    tag_flac(ALBUM / "18. Duplicate Track.flac", "Duplicate Track", "Test Artist", track=18)
    enc(dup, ALBUM / "18b. Duplicate Track.mp3", "-ar", "44100", "-c:a", "libmp3lame",
        "-b:a", "320k")
    tag_mp3(ALBUM / "18b. Duplicate Track.mp3", "Duplicate Track", "Test Artist", track=18)

    # version family: same audio, VIP suffix -> NOT a duplicate
    fam = m(19)
    enc(fam, ALBUM / "19. Family Track.flac", "-ar", "44100", "-sample_fmt", "s16",
        "-c:a", "flac")
    tag_flac(ALBUM / "19. Family Track.flac", "Family Track", "Test Artist", track=19)
    enc(fam, ALBUM / "19b. Family Track (VIP).flac", "-ar", "44100", "-sample_fmt", "s16",
        "-c:a", "flac")
    tag_flac(ALBUM / "19b. Family Track (VIP).flac", "Family Track (VIP)", "Test Artist",
             track=19)

    # fake DRM AAC (.m4p) -> reject by rule
    enc(m(20), ALBUM / "20. DRM Track.m4p", "-ar", "44100", "-c:a", "aac", "-f", "mp4")

    # torrent leftover -> silently skipped
    (ALBUM / "21. Partial Track.flac.part").write_bytes(b"\x00" * 4096)

    n = sum(1 for _ in SRC.rglob("*") if _.is_file())
    print(f"trap set generated: {n} files under {SRC}")


if __name__ == "__main__":
    main()
