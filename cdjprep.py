#!/usr/bin/env python3
"""cdjprep — prepare a raw music library for rekordbox import and CDJ-900 (2010) USB playback.

Pipeline position:  raw files -> [cdjprep] -> staging folder -> rekordbox -> USB.
This tool never writes a Pioneer database and never touches the USB stick.

Every behavioral decision references RESEARCH.md (D1..D17).
Source files are never modified or deleted. All writes go inside the staging folder.
"""

import argparse
import atexit
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unicodedata
from pathlib import Path

from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import (
    APIC, ID3, TALB, TBPM, TCON, TDRC, TIT2, TKEY, TPE1, TPE2, TRCK, TXXX,
    ID3NoHeaderError,
)
from mutagen.mp4 import MP4, MP4Cover

TOOL_VERSION = "1.0.0"

AUDIO_EXTS = {".flac", ".mp3", ".wav", ".aif", ".aiff", ".aifc", ".m4a", ".mp4",
              ".m4p", ".aac", ".ogg", ".oga", ".opus", ".wma", ".wv", ".ape", ".alac"}

# CDJ-900 official support envelope (RESEARCH.md §1, D2)
CDJ_PCM_RATES = {44100, 48000}
CDJ_MP3_RATES = {32000, 44100, 48000, 16000, 22050, 24000}
CDJ_MPEG2_RATES = {16000, 22050, 24000}
CDJ_AAC_RATES = {16000, 22050, 24000, 32000, 44100, 48000}

# D11: CDJ-900 screen renders only A-Z 0-9 + basic symbols; transliterate Cyrillic (BGN/PCGN-ish)
CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "є": "ye", "і": "i", "ї": "yi", "ґ": "g",
}
FAT32_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
FAT32_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
VERSION_TOKENS = re.compile(r"\b(vip|remix|rmx|edit|bootleg|dub|instrumental|extended|rework|refix|flip|version|mix)\b", re.I)

_print_lock = threading.Lock()
_counter_lock = threading.Lock()
_done_count = 0


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def progress(total, msg):
    global _done_count
    with _counter_lock:
        _done_count += 1
        n = _done_count
    log(f"[{n:>4}/{total}] {msg}")


# ---------------------------------------------------------------- subprocess

def run(cmd, timeout=600):
    """All child processes get closed stdin (-nostdin is added at call sites for ffmpeg/ffprobe):
    otherwise ffmpeg eats the caller's stdin and silently breaks loops."""
    return subprocess.run(
        [str(c) for c in cmd],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def ffmpeg_cmd(cfg, *args):
    return [cfg["binaries"]["ffmpeg"], "-nostdin", "-hide_banner", *args]


def ffprobe_cmd(cfg, *args):
    # ffprobe has no -nostdin option; stdin is closed via subprocess DEVNULL in run()
    return [cfg["binaries"]["ffprobe"], "-hide_banner", *args]


# ---------------------------------------------------------------- config

def load_config(path):
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["sources"]["paths"] = [Path(p).expanduser() for p in cfg["sources"]["paths"]]
    cfg["staging"]["path"] = Path(cfg["staging"]["path"]).expanduser()
    for k, v in cfg["binaries"].items():
        cfg["binaries"][k] = str(Path(v).expanduser())
    return cfg


def config_signature(cfg):
    """Hash of settings that change conversion/naming output — part of idempotency key (D16)."""
    sig = json.dumps({"target": cfg["target"], "staging_layout": cfg["staging"]["layout"],
                      "template": cfg["staging"]["filename_template"]}, sort_keys=True, default=str)
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def convert_signature(cfg):
    """Hash of settings that change the audio CONTENT of outputs. When only naming
    changed (config_signature differs but this matches), outputs are renamed in place
    instead of being re-encoded."""
    t = {k: v for k, v in cfg["target"].items() if k != "volume_gb"}
    return hashlib.sha256(json.dumps(t, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- text utils

def transliterate(s):
    """D11: to CDJ-displayable ASCII. Cyrillic mapped, accents stripped, rest dropped."""
    out = []
    for ch in s:
        lo = ch.lower()
        if lo in CYR:
            t = CYR[lo]
            out.append(t.capitalize() if ch.isupper() and t else t)
        else:
            out.append(ch)
    s = "".join(out)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if 32 <= ord(c) < 127 else " " for c in s)
    return re.sub(r"\s+", " ", s).strip()


def sanitize_component(s, maxlen=120):
    """FAT32-legal single path component (RESEARCH.md §2 filenames, D12)."""
    s = FAT32_ILLEGAL.sub("", s)
    s = s.rstrip(" .")
    s = re.sub(r"\s+", " ", s).strip()
    if s.split(".")[0].upper() in FAT32_RESERVED:
        s = "_" + s
    if len(s) > maxlen:
        s = s[:maxlen].rstrip(" .")
    return s or "Unknown"


def norm_title_key(s):
    s = re.sub(r"^\d+[\s.\-_]+", "", s.lower())
    # "(Original Mix)" is Beatport naming for the original, not a distinct version —
    # it must not trigger the VIP/remix veto in duplicate detection (D15)
    s = re.sub(r"[(\[]?\s*original\s+mix\s*[)\]]?", " ", s)
    return re.sub(r"[^a-z0-9а-яё]+", " ", s).strip()


# ---------------------------------------------------------------- probing

def ffprobe_info(cfg, path):
    r = run(ffprobe_cmd(cfg, "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", str(path)))
    if r.returncode != 0:
        return None, (r.stderr.decode("utf-8", "replace").strip() or "ffprobe failed")
    try:
        data = json.loads(r.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None, "ffprobe produced invalid JSON"
    astreams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not astreams:
        return None, "no audio stream"
    a = astreams[0]
    fmt = data.get("format", {})
    dur = float(a.get("duration") or fmt.get("duration") or 0)
    bits = int(a.get("bits_per_raw_sample") or a.get("bits_per_sample") or 0)
    sfmt = a.get("sample_fmt", "")
    if not bits:
        bits = {"s16": 16, "s16p": 16, "s32": 32, "s32p": 32, "flt": 32, "fltp": 32,
                "dbl": 64, "dblp": 64, "u8": 8, "u8p": 8}.get(sfmt, 0)
    return {
        "codec": a.get("codec_name", ""),
        "codec_tag": (a.get("codec_tag") or "0x0000").lower(),
        "profile": a.get("profile", ""),
        "sample_rate": int(a.get("sample_rate") or 0),
        "sample_fmt": sfmt,
        "bits": bits,
        "is_float": sfmt.startswith(("flt", "dbl")),
        "channels": int(a.get("channels") or 0),
        "duration": dur,
        "bit_rate": int(a.get("bit_rate") or fmt.get("bit_rate") or 0),
        "format_name": fmt.get("format_name", ""),
        "size": int(fmt.get("size") or 0),
    }, None


def is_aifc(path):
    try:
        with open(path, "rb") as f:
            head = f.read(12)
        return head[:4] == b"FORM" and head[8:12] == b"AIFC"
    except OSError:
        return False


def wav_fmt_tag(path):
    """Read the raw fmt-chunk format tag. ffprobe resolves WAVE_FORMAT_EXTENSIBLE (0xFFFE)
    to the inner GUID codec and reports codec_tag 0x0001, hiding exactly the header the
    CDJ rejects with E-8305 (D3) — so we must parse the header bytes ourselves."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return None
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    return None
                cid = hdr[:4]
                size = int.from_bytes(hdr[4:8], "little")
                if cid == b"fmt ":
                    return int.from_bytes(f.read(2), "little")
                f.seek(size + (size & 1), 1)
    except OSError:
        return None


def integrity_check(cfg, path, header_duration):
    """D4: fully decode the file. Returns (ok, reason, pcm_md5, decoded_duration).
    Decoded PCM is normalized to s16le so the hash is comparable across containers (D15 layer 1)."""
    r = run(ffmpeg_cmd(cfg, "-v", "error", "-progress", "pipe:1",
                       "-i", str(path), "-map", "0:a:0",
                       "-c:a", "pcm_s16le", "-f", "md5", "pipe:1"), timeout=900)
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace").strip()
    md5 = None
    decoded_us = 0
    for line in out.splitlines():
        if line.startswith("MD5="):
            md5 = line.split("=", 1)[1].strip()
        elif line.startswith("out_time_us="):
            try:
                decoded_us = max(decoded_us, int(line.split("=", 1)[1]))
            except ValueError:
                pass
    decoded = decoded_us / 1e6
    if r.returncode != 0:
        return False, f"decode failed: {err.splitlines()[0] if err else 'unknown error'}", md5, decoded
    if err:
        return False, f"decode errors: {err.splitlines()[0]}", md5, decoded
    if header_duration > 1 and decoded < header_duration - max(1.0, header_duration * 0.02):
        return False, (f"truncated: header says {header_duration:.1f}s, "
                       f"decodes to {decoded:.1f}s"), md5, decoded
    return True, "", md5, decoded


# ---------------------------------------------------------------- classification

def classify(cfg, path, p):
    """Decide copy / convert per RESEARCH.md D1-D3. Returns (action, reasons, warnings)."""
    ext = path.suffix.lower()
    warns = []
    if p["channels"] == 1:
        warns.append("mono — CDJ-900 playability unconfirmed, verify on deck (D17)")

    def conv(reason):
        return "convert", [reason], warns

    if p["codec"] == "mp3" and ext == ".mp3":
        if p["sample_rate"] not in CDJ_MP3_RATES:
            return conv(f"MP3 at {p['sample_rate']} Hz outside CDJ-900 spec (MPEG-2.5)")
        if p["sample_rate"] in CDJ_MPEG2_RATES:
            warns.append(f"MPEG-2 MP3 at {p['sample_rate']} Hz — plays, but low quality source")
        return "copy", ["MP3 within CDJ-900 spec — copied bit-exact, no generation loss (D2)"], warns

    if p["codec"] == "aac" and ext in {".m4a", ".mp4", ".aac"}:
        if p["profile"] not in ("LC", "Main", ""):
            return conv(f"AAC profile '{p['profile']}' — CDJ-900 accepts AAC-LC only")
        if p["sample_rate"] not in CDJ_AAC_RATES:
            return conv(f"AAC at {p['sample_rate']} Hz outside CDJ-900 spec")
        return "copy", ["AAC-LC within CDJ-900 spec — copied bit-exact (D2)"], warns

    if ext == ".wav" and p["codec"].startswith("pcm_"):
        problems = []
        if p["codec_tag"] == "0xfffe" or wav_fmt_tag(path) == 0xFFFE:
            problems.append("WAVE_FORMAT_EXTENSIBLE header — rejected by CDJ with E-8305 (D3)")
        if p["is_float"]:
            problems.append("32-bit float WAV — CDJ-900 accepts integer PCM only")
        elif p["codec"] not in ("pcm_s16le", "pcm_s24le"):
            problems.append(f"WAV codec {p['codec']} outside 16/24-bit integer PCM spec")
        if p["sample_rate"] not in CDJ_PCM_RATES:
            problems.append(f"WAV at {p['sample_rate']} Hz — CDJ-900 plays 44.1/48 kHz only")
        if problems:
            return "convert", problems, warns
        return "copy", ["WAV 16/24-bit PCM 44.1/48 kHz plain header — within spec (D2)"], \
            warns + ["WAV tags are unreliable in rekordbox (RIFF INFO) — consider AIFF (D10)"]

    if ext in {".aif", ".aiff", ".aifc"} and p["codec"].startswith("pcm_"):
        if is_aifc(path):
            return conv("AIFF-C container — CDJ-900 accepts uncompressed AIFF only")
        if p["codec"] not in ("pcm_s16be", "pcm_s24be"):
            return conv(f"AIFF codec {p['codec']} outside 16/24-bit PCM spec")
        if p["sample_rate"] not in CDJ_PCM_RATES:
            return conv(f"AIFF at {p['sample_rate']} Hz — CDJ-900 plays 44.1/48 kHz only")
        return "copy", ["AIFF 16/24-bit PCM 44.1/48 kHz — within spec (D2)"], warns

    name = {"flac": "FLAC", "alac": "ALAC", "vorbis": "Ogg Vorbis", "opus": "Opus",
            "wmav2": "WMA"}.get(p["codec"], f"{p['codec']}/{ext}")
    return conv(f"{name} not playable on CDJ-900 (MP3/AAC/WAV/AIFF only)")


# ---------------------------------------------------------------- tags

def _id3_text(tags, frame):
    f = tags.get(frame)
    return str(f.text[0]) if f and f.text else ""


def read_tags(path, ext):
    """Unified tag read. Returns dict of strings + art tuple (bytes, mime) or None."""
    t = {"title": "", "artist": "", "album": "", "albumartist": "", "track": None,
         "tracktotal": None, "year": "", "genre": "", "art": None}
    try:
        if ext == ".flac":
            f = FLAC(path)

            def v(key):
                return f.get(key, [""])[0]
            t.update(title=v("title"), artist=v("artist"), album=v("album"),
                     albumartist=v("albumartist"), genre=v("genre"))
            t["year"] = (v("date") or v("year"))[:4]
            tn = v("tracknumber")
            if tn:
                m = re.match(r"(\d+)(?:/(\d+))?", tn)
                if m:
                    t["track"] = int(m.group(1))
                    t["tracktotal"] = int(m.group(2)) if m.group(2) else None
            tt = v("tracktotal") or v("totaltracks")
            if tt.isdigit():
                t["tracktotal"] = int(tt)
            if f.pictures:
                t["art"] = (f.pictures[0].data, f.pictures[0].mime)
        elif ext in {".m4a", ".mp4", ".m4p", ".aac"}:
            try:
                f = MP4(path)
            except Exception:
                return t

            def m4(key):
                v = f.tags.get(key) if f.tags else None
                return str(v[0]) if v else ""
            t.update(title=m4("\xa9nam"), artist=m4("\xa9ART"), album=m4("\xa9alb"),
                     albumartist=m4("aART"), genre=m4("\xa9gen"))
            t["year"] = m4("\xa9day")[:4]
            trkn = f.tags.get("trkn") if f.tags else None
            if trkn:
                t["track"], t["tracktotal"] = trkn[0][0] or None, trkn[0][1] or None
            covr = f.tags.get("covr") if f.tags else None
            if covr:
                mime = "image/png" if covr[0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
                t["art"] = (bytes(covr[0]), mime)
        else:  # ID3 carriers: mp3, aiff, wav
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                return t
            t.update(title=_id3_text(tags, "TIT2"), artist=_id3_text(tags, "TPE1"),
                     album=_id3_text(tags, "TALB"), albumartist=_id3_text(tags, "TPE2"),
                     genre=_id3_text(tags, "TCON"))
            t["year"] = (_id3_text(tags, "TDRC") or _id3_text(tags, "TYER"))[:4]
            trck = _id3_text(tags, "TRCK")
            m = re.match(r"(\d+)(?:/(\d+))?", trck)
            if m:
                t["track"] = int(m.group(1))
                t["tracktotal"] = int(m.group(2)) if m.group(2) else None
            apics = tags.getall("APIC")
            if apics:
                t["art"] = (apics[0].data, apics[0].mime)
    except Exception:
        pass
    return t


def tags_from_path(path, src_root):
    """Fallback tags from 'Artist - Album (Year)/NN. Track.ext' structure."""
    t = {}
    stem = path.stem
    m = re.match(r"^(\d{1,3})[\s.\-_]+(.*)$", stem)
    if m:
        t["track"] = int(m.group(1))
        stem = m.group(2)
    m = re.match(r"^(.*?)\s+-\s+(.*)$", stem)
    if m:
        t["artist_guess"], t["title"] = m.group(1), m.group(2)
    else:
        t["title"] = stem
    folder = path.parent.name if path.parent != src_root else ""
    m = re.match(r"^(?:\[[^\]]*\]\s*)?(.+?)\s+-\s+(.+?)\s*[\(\[](\d{4})[\)\]]", folder)
    if m:
        t.setdefault("artist_guess", m.group(1))
        t["album"], t["year"] = m.group(2), m.group(3)
    elif " - " in folder:
        a, _, b = folder.partition(" - ")
        t.setdefault("artist_guess", a)
        t["album"] = b
    return t


def merge_tags(tagged, from_path):
    out = dict(tagged)
    if not out["title"] and from_path.get("title"):
        out["title"] = from_path["title"]
    if not out["artist"] and from_path.get("artist_guess"):
        out["artist"] = from_path["artist_guess"]
    if not out["album"] and from_path.get("album"):
        out["album"] = from_path["album"]
    if not out["year"] and from_path.get("year"):
        out["year"] = from_path["year"]
    if out["track"] is None and from_path.get("track") is not None:
        out["track"] = from_path["track"]
    return out


def prepare_artwork(cfg, art, tmpdir):
    """D13: re-encode embedded art to JPEG capped at 800x800. Returns jpeg bytes or None.
    Artwork is optional — any failure here must degrade to 'no art', never fail the track."""
    if not art:
        return None
    data, _mime = art
    src = tmpdir / f"art_in_{threading.get_ident()}"
    dst = tmpdir / f"art_out_{threading.get_ident()}.jpg"
    try:
        tmpdir.mkdir(parents=True, exist_ok=True)
        src.write_bytes(data)
        r = run(ffmpeg_cmd(cfg, "-v", "error", "-y", "-i", str(src),
                           "-vf", "scale=w='min(iw,800)':h='min(ih,800)':force_original_aspect_ratio=decrease",
                           "-frames:v", "1", "-q:v", "3", "-f", "mjpeg", str(dst)))
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return dst.read_bytes()
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        for f in (src, dst):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass


def _enc(s):
    return 0 if s.isascii() else 1  # latin1 for ASCII, UTF-16 otherwise (ID3v2.3-safe)


def build_id3(rec, jpeg_art):
    """Fresh ID3v2.3 frame set from the record's final (transliterated) tags (D10, D11)."""
    tg, orig = rec["tags_final"], rec["tags_orig"]
    tags = ID3()

    def add_text(cls, val):
        if val:
            tags.add(cls(encoding=_enc(val), text=[val]))
    add_text(TIT2, tg["title"])
    add_text(TPE1, tg["artist"])
    add_text(TALB, tg["album"])
    add_text(TPE2, tg["albumartist"])
    add_text(TCON, tg["genre"])
    if tg["track"] is not None:
        trck = str(tg["track"]) + (f"/{tg['tracktotal']}" if tg["tracktotal"] else "")
        tags.add(TRCK(encoding=0, text=[trck]))
    if tg["year"]:
        tags.add(TDRC(encoding=0, text=[tg["year"]]))
    an = rec.get("analysis", {})
    if an.get("bpm"):
        tags.add(TBPM(encoding=0, text=[f"{an['bpm']:g}"]))
    if an.get("key"):
        tags.add(TKEY(encoding=0, text=[an["key"]]))
    if an.get("lufs_i") is not None:
        tags.add(TXXX(encoding=0, desc="LUFS-I", text=[f"{an['lufs_i']:.1f}"]))
    if an.get("true_peak_db") is not None:
        tags.add(TXXX(encoding=0, desc="TRUE PEAK DBTP", text=[f"{an['true_peak_db']:.2f}"]))
    for field in ("title", "artist", "album"):
        if orig.get(field) and orig[field] != tg[field]:
            tags.add(TXXX(encoding=1, desc=f"ORIG {field.upper()}", text=[orig[field]]))
    if jpeg_art:
        tags.add(APIC(encoding=0, mime="image/jpeg", type=3, desc="Cover", data=jpeg_art))
    return tags


def write_tags(cfg, rec, tmpdir):
    """Write normalized ID3v2.3 (or MP4 atoms) onto the OUTPUT file."""
    dst = Path(rec["output"])
    ext = dst.suffix.lower()
    jpeg_art = prepare_artwork(cfg, rec.pop("_art", None), tmpdir)
    if ext == ".aac":  # raw ADTS stream — no standard tag container to write into
        rec["warnings"].append("raw .aac copy: tags not writable (ADTS has no tag container)")
        return
    if ext in {".m4a", ".mp4"}:
        f = MP4(dst)
        if f.tags is None:
            f.add_tags()
        tg, an = rec["tags_final"], rec.get("analysis", {})
        if tg["title"]:
            f.tags["\xa9nam"] = [tg["title"]]
        if tg["artist"]:
            f.tags["\xa9ART"] = [tg["artist"]]
        if tg["album"]:
            f.tags["\xa9alb"] = [tg["album"]]
        if tg["albumartist"]:
            f.tags["aART"] = [tg["albumartist"]]
        if tg["genre"]:
            f.tags["\xa9gen"] = [tg["genre"]]
        if tg["year"]:
            f.tags["\xa9day"] = [tg["year"]]
        if tg["track"] is not None:
            f.tags["trkn"] = [(tg["track"], tg["tracktotal"] or 0)]
        if an.get("bpm"):
            f.tags["tmpo"] = [round(an["bpm"])]
        if jpeg_art:
            f.tags["covr"] = [MP4Cover(jpeg_art, imageformat=MP4Cover.FORMAT_JPEG)]
        f.save()
        return
    if ext == ".wav":
        # D10: mutagen cannot write RIFF INFO; rekordbox WAV tag support is weak anyway.
        rec["warnings"].append("WAV copy: tags left untouched (RIFF INFO not writable; D10)")
        return
    tags = build_id3(rec, jpeg_art)
    tags.update_to_v23()
    if ext == ".mp3":
        # ID3.save on an MP3 path rewrites the leading ID3v2 block and can drop ID3v1.
        tags.save(dst, v2_version=3, v1=0)
    else:  # .aiff / .aif — ID3 must live inside the IFF 'ID3 ' chunk, so go via AIFF
        audio = AIFF(dst)
        if audio.tags is None:
            audio.add_tags()
        for k in list(audio.tags.keys()):
            del audio.tags[k]
        for k, frame in tags.items():
            audio.tags[k] = frame
        audio.save(v2_version=3)


# ---------------------------------------------------------------- naming (D12)

def build_output_name(cfg, rec):
    tg = rec["tags_final"]
    tpl = cfg["staging"]["filename_template"]
    an = rec.get("analysis", {})
    fields = {
        "artist": tg["artist"] or "Unknown Artist",
        "title": tg["title"] or Path(rec["source"]).stem,
        "album": tg["album"], "year": tg["year"],
        "track": f"{tg['track']:02d}" if tg["track"] is not None else "",
        "bpm": str(round(an["bpm"])) if an.get("bpm") else "",
    }
    name = tpl.format_map(fields)
    name = re.sub(r"\s+\.\s+", " ", name)          # "{bpm} {track}." with empty track
    name = re.sub(r"^[\s.\-]+", "", name)          # dangling separators when bpm/track empty
    name = re.sub(r"\(\s*\)|\[\s*\]", "", name)
    return sanitize_component(transliterate(name))


def plan_outputs(cfg, records, src_roots):
    staging = cfg["staging"]["path"]
    used = {}
    for rec in records:  # seed with outputs already claimed by previous runs (skip records)
        if rec.get("output_rel"):
            used[rec["output_rel"]] = rec["source"]
    for rec in records:
        # "skip" records normally keep their stored name; rename candidates arrive
        # with output_rel cleared and must be re-planned like everything else
        if rec["action"] not in ("copy", "convert", "skip") or rec.get("output_rel"):
            continue
        src = Path(rec["source"])
        ext = src.suffix.lower() if rec["action"] == "copy" else ".aiff"
        if ext == ".aif":
            ext = ".aiff"
        if cfg["staging"]["layout"] == "release_folders":
            root = next((r for r in src_roots if src.is_relative_to(r)), None)
            folder = src.parent.name if (root and src.parent != root) else "Singles"
            folder = sanitize_component(transliterate(folder))
        else:
            folder = ""
        base = build_output_name(cfg, rec)
        rel = f"{folder}/{base}{ext}" if folder else f"{base}{ext}"
        n = 2
        while rel in used and used[rel] != rec["source"]:
            rel = (f"{folder}/{base} ({n}){ext}" if folder else f"{base} ({n}){ext}")
            n += 1
        used[rel] = rec["source"]
        rec["output_rel"] = rel
        rec["output"] = str(staging / rel)
        if len(rel) > 200:
            rec["warnings"].append(f"output path {len(rel)} chars — near the 256-char "
                                   "rekordbox/PRO DJ LINK limit (D12)")
        depth = rel.count("/") + 1
        if depth > 8:
            rec["warnings"].append(f"nesting depth {depth} exceeds CDJ-900 limit of 8 (D12)")


# ---------------------------------------------------------------- conversion (D1, D5, D6)

def convert_file(cfg, rec):
    src, dst = Path(rec["source"]), Path(rec["output"])
    p = rec["probe"]
    t = cfg["target"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    out_rate = p["sample_rate"] if p["sample_rate"] in CDJ_PCM_RATES else 44100
    depth = int(t["bit_depth"])
    codec = "pcm_s16be" if depth == 16 else "pcm_s24be"
    out_fmt = "s16" if depth == 16 else "s32"
    reduces_depth = p["is_float"] or (p["bits"] or 32) > depth
    af = [f"aresample=osr={out_rate}:filter_size={t['resample_filter_size']}:out_sample_fmt={out_fmt}"]
    steps = []
    if p["sample_rate"] != out_rate:
        steps.append(f"resample {p['sample_rate']}->{out_rate} Hz (swr filter_size="
                     f"{t['resample_filter_size']}, D5)")
    if reduces_depth:
        af[0] += f":dither_method={t['dither_method']}"
        steps.append(f"bit depth {'float' if p['is_float'] else p['bits']}->{depth} "
                     f"with {t['dither_method']} dither (D6)")
    else:
        steps.append(f"bit depth {p['bits'] or '?'}->{depth}, no dither needed (D6)")
    r = run(ffmpeg_cmd(cfg, "-v", "error", "-y", "-i", str(src),
                       "-map", "0:a:0", "-map_metadata", "-1", "-vn",
                       "-af", af[0], "-c:a", codec, "-f", "aiff", str(dst)), timeout=1800)
    if r.returncode != 0:
        raise RuntimeError("conversion failed: "
                           + r.stderr.decode("utf-8", "replace").strip().splitlines()[0])
    rec["steps"] = steps


# ---------------------------------------------------------------- analysis (D7-D9, D14)

def detect_bpm(cfg, src):
    """BPM of any readable audio file (runs on SOURCES during audit so the value is
    available for {bpm} in the filename template before outputs are planned)."""
    lo, hi = cfg["analysis"]["bpm_min_expected"], cfg["analysis"]["bpm_max_expected"]

    def aubio_on(path):
        r = run([cfg["binaries"]["aubio"], "tempo", str(path)], timeout=300)
        m = re.search(r"([\d.]+)\s*bpm", r.stdout.decode("utf-8", "replace"))
        return float(m.group(1)) if m else None
    bpm = aubio_on(src)
    if bpm is None:  # fallback: decode to wav first (aubio may lack the codec)
        with tempfile.TemporaryDirectory(prefix="cdjprep_bpm_") as td:
            tmp = Path(td) / "a.wav"
            r = run(ffmpeg_cmd(cfg, "-v", "error", "-y", "-i", str(src), "-ac", "1",
                               "-ar", "44100", "-c:a", "pcm_s16le", str(tmp)))
            if r.returncode == 0:
                bpm = aubio_on(tmp)
    if bpm is None:
        return None, "bpm detection failed"
    note = ""
    while bpm < lo:
        bpm *= 2
        note = "octave-corrected x2 (D14)"
    while bpm > hi:
        bpm /= 2
        note = "octave-corrected /2 (D14)"
    return round(bpm, 1), note


def detect_key(cfg, rec):
    kf = cfg["binaries"]["keyfinder"]
    if not Path(kf).exists():
        return None
    r = run([kf, rec["output"]], timeout=300)
    key = r.stdout.decode("utf-8", "replace").strip()
    return key if r.returncode == 0 and key and len(key) <= 3 else None


def measure_audio(cfg, rec):
    """One-pass loudness/true-peak/DC/leading-silence on the OUTPUT file (D7-D9)."""
    an = {}
    r = run(ffmpeg_cmd(cfg, "-v", "info", "-i", rec["output"], "-map", "0:a:0",
                       "-af", "silencedetect=n=-60dB:d=0.5,astats=measure_perchannel=none,"
                              "ebur128=peak=true",
                       "-f", "null", "-"), timeout=900)
    err = r.stderr.decode("utf-8", "replace")

    def last(pattern):
        """ebur128 prints running values during the pass; the summary block comes last."""
        hits = re.findall(pattern, err)
        return hits[-1] if hits else None
    v = last(r"\bI:\s*(-?[\d.]+)\s*LUFS")
    if v:
        an["lufs_i"] = float(v)
    v = last(r"\bLRA:\s*(-?[\d.]+)\s*LU\b")
    if v:
        an["lra"] = float(v)
    v = last(r"True peak:\s*\n?\s*Peak:\s*(-?[\d.]+|-?inf)\s*dBFS")
    if v and "inf" not in v:
        an["true_peak_db"] = float(v)
    v = last(r"DC offset:\s*(-?[\d.eE+\-]+)")
    if v:
        try:
            an["dc_offset"] = float(v)
        except ValueError:
            pass
    sil = re.search(r"silence_start:\s*(-?[\d.]+)", err)
    if sil and abs(float(sil.group(1))) < 0.05:
        end = re.search(r"silence_end:\s*([\d.]+)", err)
        if end:
            an["leading_silence_s"] = round(float(end.group(1)), 2)
    a = cfg["analysis"]
    if an.get("true_peak_db") is not None:
        if an["true_peak_db"] > 0:
            rec["warnings"].append(f"CLIPPING: true peak {an['true_peak_db']:+.2f} dBTP > 0 "
                                   "— intersample overs baked into the master (D8)")
        elif an["true_peak_db"] > a["true_peak_warn_db"]:
            rec["warnings"].append(f"hot master: true peak {an['true_peak_db']:+.2f} dBTP "
                                   f"above {a['true_peak_warn_db']} dBTP ceiling (D8)")
    if an.get("dc_offset") is not None and abs(an["dc_offset"]) > a["dc_offset_warn"]:
        rec["warnings"].append(f"DC offset {an['dc_offset']:+.4f} — not fixed, per D9")
    if an.get("leading_silence_s", 0) > a["leading_silence_warn_s"]:
        rec["warnings"].append(f"leading silence {an['leading_silence_s']:.1f}s — not trimmed "
                               "(Auto Cue skips it, D9)")
    return an


def fingerprint(cfg, path):
    fp = cfg["binaries"]["fpcalc"]
    if not Path(fp).exists():
        return None
    r = run([fp, "-raw", "-length", "120", str(path)], timeout=300)
    m = re.search(r"FINGERPRINT=([\d,\-]+)", r.stdout.decode("utf-8", "replace"))
    if not m:
        return None
    return [int(x) & 0xFFFFFFFF for x in m.group(1).split(",")]


# ---------------------------------------------------------------- duplicates (D15)

def fp_similarity(a, b):
    best = 0.0
    for off in range(-3, 4):
        aa = a[max(0, off):]
        bb = b[max(0, -off):]
        n = min(len(aa), len(bb))
        if n < 16:
            continue
        errs = sum((aa[i] ^ bb[i]).bit_count() for i in range(n))
        best = max(best, 1.0 - errs / (32.0 * n))
    return best


def find_duplicates(cfg, records):
    live = [r for r in records if r["action"] in ("copy", "convert", "skip") and r["probe"]]
    groups = []
    # Layer 1: identical decoded PCM
    by_md5 = {}
    for r in live:
        if r.get("pcm_md5"):
            by_md5.setdefault(r["pcm_md5"], []).append(r)
    for md5, rs in by_md5.items():
        if len(rs) > 1:
            groups.append({"kind": "identical audio (same decoded PCM, D15 L1)",
                           "files": [x["source"] for x in rs]})
    # Layer 2: chromaprint similarity
    gate = cfg["duplicates"]["duration_gate_s"]
    thr = cfg["duplicates"]["fingerprint_threshold"]
    fps = [r for r in live if r.get("fp")]
    seen_pairs = set()
    for i in range(len(fps)):
        for j in range(i + 1, len(fps)):
            a, b = fps[i], fps[j]
            if a.get("pcm_md5") and a.get("pcm_md5") == b.get("pcm_md5"):
                continue  # already in layer 1
            if abs(a["probe"]["duration"] - b["probe"]["duration"]) > gate:
                continue
            sim = fp_similarity(a["fp"], b["fp"])
            if sim < thr:
                continue
            ta = norm_title_key(a["tags_orig"]["title"] or Path(a["source"]).stem)
            tb = norm_title_key(b["tags_orig"]["title"] or Path(b["source"]).stem)
            kind = "probable duplicate (acoustic match, D15 L2)"
            if ta != tb and (VERSION_TOKENS.search(ta) or VERSION_TOKENS.search(tb)):
                kind = "version family (VIP/remix token differs) — NOT merged (D15)"
            key = tuple(sorted((a["source"], b["source"])))
            if key not in seen_pairs:
                seen_pairs.add(key)
                groups.append({"kind": kind, "similarity": round(sim, 3),
                               "files": [a["source"], b["source"]]})
    # Layer 3: identical normalized tags
    by_tag = {}
    for r in live:
        t = r["tags_orig"]
        if t["artist"] and t["title"]:
            by_tag.setdefault((norm_title_key(t["artist"]), norm_title_key(t["title"])),
                              []).append(r)
    layered = {f for g in groups for f in g["files"]}
    for (_, _), rs in by_tag.items():
        if len(rs) > 1 and not all(x["source"] in layered for x in rs):
            groups.append({"kind": "possible duplicate (identical artist+title tags, D15 L3)",
                           "files": [x["source"] for x in rs]})
    return groups


# ---------------------------------------------------------------- worker

def audit_file(cfg, path, src_roots, total):
    rec = {"source": str(path), "action": None, "reasons": [], "warnings": [],
           "steps": [], "probe": None, "analysis": {}, "output": None, "output_rel": None}
    probe, err = ffprobe_info(cfg, path)
    if probe is None:
        rec["action"] = "reject"
        rec["reasons"] = [f"unreadable: {err}"]
        progress(total, f"REJECT   {path.name}  ({err})")
        return rec
    rec["probe"] = probe
    if path.suffix.lower() == ".m4p" or probe["codec_tag"] in ("drms",):
        rec["action"] = "reject"
        rec["reasons"] = ["DRM-protected AAC — cannot be played or converted (RESEARCH §1)"]
        progress(total, f"REJECT   {path.name}  (DRM)")
        return rec
    ok, why, md5, decoded = integrity_check(cfg, path, probe["duration"])
    rec["pcm_md5"] = md5
    rec["decoded_duration"] = round(decoded, 3)
    if not ok:
        rec["action"] = "reject"
        rec["reasons"] = [why]
        progress(total, f"REJECT   {path.name}  ({why})")
        return rec
    action, reasons, warns = classify(cfg, path, probe)
    rec["action"] = action
    rec["reasons"] = reasons
    rec["warnings"] = warns
    src_root = next((r for r in src_roots if path.is_relative_to(r)), path.parent)
    tagged = read_tags(path, path.suffix.lower())
    merged = merge_tags(tagged, tags_from_path(path, src_root))
    rec["_art"] = merged.pop("art", None)
    rec["tags_orig"] = merged
    final = dict(merged)
    for field in ("title", "artist", "album", "albumartist", "genre"):
        final[field] = transliterate(final[field] or "")
    rec["tags_final"] = final
    missing = [f for f in ("artist", "title") if not final[f]]
    if missing:
        rec["warnings"].append(f"empty critical tag(s): {', '.join(missing)} (D10)")
    if cfg["analysis"]["bpm"]:  # on the source, so {bpm} is usable in output names
        bpm, note = detect_bpm(cfg, path)
        if bpm:
            rec["analysis"]["bpm"] = bpm
            if note:
                rec["analysis"]["bpm_note"] = note
        else:
            rec["warnings"].append("BPM detection failed on source")
    if cfg["duplicates"]["enabled"]:
        rec["fp"] = fingerprint(cfg, path)
    progress(total, f"{action.upper():8} {path.name}  ({reasons[0]})")
    return rec


def materialize_file(cfg, rec, tmpdir, total):
    src, dst = Path(rec["source"]), Path(rec["output"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    if rec["action"] == "copy":
        shutil.copy2(src, dst)
        rec["steps"] = ["copied bit-exact (D2)"] + rec["steps"]
    else:
        convert_file(cfg, rec)
    a = cfg["analysis"]
    if a["bpm"] and not rec["analysis"].get("bpm"):  # normally set during audit
        bpm, note = detect_bpm(cfg, rec["output"])
        if bpm:
            rec["analysis"]["bpm"] = bpm
            if note:
                rec["analysis"]["bpm_note"] = note
        else:
            rec["warnings"].append("BPM detection failed")
    if a["key"]:
        key = detect_key(cfg, rec)
        if key:
            rec["analysis"]["key"] = key
        else:
            rec["warnings"].append("key detection unavailable/failed")
    if a["loudness"]:
        rec["analysis"].update(measure_audio(cfg, rec))
    write_tags(cfg, rec, tmpdir)
    rec["output_size"] = dst.stat().st_size
    an = rec["analysis"]
    info = " ".join(x for x in (
        f"{an.get('bpm', '?')}bpm", an.get("key", ""),
        f"{an.get('lufs_i'):.1f}LUFS" if an.get("lufs_i") is not None else "",
        f"TP{an.get('true_peak_db'):+.1f}" if an.get("true_peak_db") is not None else "",
    ) if x)
    progress(total, f"DONE     {rec['output_rel']}  [{info}]")


# ---------------------------------------------------------------- reporting

def human_summary(cfg, records, dup_groups, dry_run, elapsed):
    by = {"copy": [], "convert": [], "reject": [], "skip": []}
    for r in records:
        by.setdefault(r["action"], []).append(r)
    out_bytes = sum(r.get("output_size", 0) for r in records)
    if dry_run:  # estimate: copies keep size; conversions ~ rate*depth*ch*dur
        out_bytes = 0
        for r in by["copy"] + by["skip"]:
            out_bytes += (r["probe"] or {}).get("size", 0)
        for r in by["convert"]:
            p = r["probe"] or {}
            rate = p.get("sample_rate") if p.get("sample_rate") in CDJ_PCM_RATES else 44100
            out_bytes += int(p.get("duration", 0) * rate * (cfg["target"]["bit_depth"] / 8)
                             * max(p.get("channels", 2), 1))
    vol = cfg["target"]["volume_gb"] * 1e9
    lines = ["", "=" * 72,
             f"cdjprep {'DRY RUN — nothing written' if dry_run else 'run complete'} "
             f"({elapsed:.0f}s)", "=" * 72,
             f"  processed: {len(records)} files",
             f"  copied as-is: {len(by['copy'])}   converted to "
             f"AIFF {cfg['target']['bit_depth']}-bit: {len(by['convert'])}   "
             f"skipped (already done): {len(by['skip'])}   rejected: {len(by['reject'])}",
             f"  staging size{' (estimated)' if dry_run else ''}: {out_bytes / 1e9:.2f} GB "
             f"-> {'FITS' if out_bytes <= vol else 'DOES NOT FIT'} a "
             f"{cfg['target']['volume_gb']:g} GB volume",
             ]
    if by["reject"]:
        lines.append("\n  REJECTED (never reach staging):")
        for r in by["reject"]:
            lines.append(f"    - {r['source']}\n        {r['reasons'][-1]}")
    warned = [r for r in records if r["warnings"]]
    if warned:
        lines.append(f"\n  WARNINGS ({len(warned)} files):")
        for r in warned:
            lines.append(f"    - {Path(r['source']).name}")
            for w in r["warnings"]:
                lines.append(f"        ! {w}")
    if dup_groups:
        lines.append(f"\n  DUPLICATES ({len(dup_groups)} groups — nothing deleted, review "
                     "manually):")
        for g in dup_groups:
            sim = f" sim={g['similarity']}" if "similarity" in g else ""
            lines.append(f"    * {g['kind']}{sim}")
            for f in g["files"]:
                lines.append(f"        {f}")
    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(prog="cdjprep", description=__doc__)
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.toml"))
    ap.add_argument("--dry-run", action="store_true",
                    help="audit + plan + duplicate report; write nothing")
    ap.add_argument("--force", action="store_true", help="reprocess even unchanged sources")
    ap.add_argument("--jobs", type=int, default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    jobs = args.jobs or cfg["run"]["jobs"] or os.cpu_count() or 4
    staging = cfg["staging"]["path"]
    state_dir = staging / ".cdjprep"
    manifest_path = state_dir / "manifest.json"
    sig = config_signature(cfg)

    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            manifest = {}

    files, n_part = [], 0
    for root in cfg["sources"]["paths"]:
        if not root.exists():
            log(f"warning: source path does not exist: {root}")
            continue
        for p in sorted(root.rglob("*")):
            if p.name.startswith("."):
                continue
            if p.suffix.lower() == ".part" or p.name.endswith(".part"):
                n_part += 1  # D4: skipped silently (counted, not reported per-file)
                continue
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                files.append(p)
    if not files:
        log("no audio files found in sources")
        return 1
    log(f"cdjprep {TOOL_VERSION} — {len(files)} audio files "
        f"({n_part} .part skipped), {jobs} workers"
        f"{', DRY RUN' if args.dry_run else ''}")

    csig = convert_signature(cfg)
    t0 = time.time()
    records, todo, stale = [], [], {}
    for p in files:
        key = str(p.resolve())
        st = p.stat()
        m = manifest.get(key)
        rec = m.get("record") if m else None
        fresh = (m and not args.force and m.get("size") == st.st_size
                 and m.get("mtime_ns") == st.st_mtime_ns)
        out_ok = bool(rec and rec.get("output") and Path(rec["output"]).exists())
        retryable = bool(rec and rec["action"] == "reject"
                         and any("processing error" in x for x in rec["reasons"]))
        if (fresh and m.get("config_sig") == sig and not retryable
                and (rec["action"] == "reject" or out_ok)):
            rec["action"] = "skip" if rec["action"] in ("copy", "convert") else rec["action"]
            records.append(rec)
        elif (fresh and out_ok and m.get("convert_sig") == csig
              and rec["action"] in ("copy", "convert", "skip")):
            # audio-affecting settings unchanged — only naming/layout changed:
            # rename the existing output instead of re-encoding it
            rec["action"] = "skip"
            rec["_old_output"] = rec["output"]
            rec["output"] = rec["output_rel"] = None
            records.append(rec)
        else:
            if rec and rec.get("output"):
                stale[key] = rec["output"]  # reprocessed under a possibly new name
            todo.append(p)

    src_roots = cfg["sources"]["paths"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        new_recs = list(ex.map(lambda p: audit_file(cfg, p, src_roots, len(todo)), todo))
    records.extend(new_recs)

    plan_outputs(cfg, records, src_roots)
    dup_groups = find_duplicates(cfg, records) if cfg["duplicates"]["enabled"] else []

    if args.dry_run:
        log("\nPLAN:")
        for r in sorted(records, key=lambda x: x["source"]):
            tgt = f" -> {r['output_rel']}" if r.get("output_rel") else ""
            log(f"  {r['action'].upper():8} {r['source']}{tgt}")
            if r.get("_old_output") and r.get("output") and r["_old_output"] != r["output"]:
                log("           note: existing output will be RENAMED (naming change only)")
            for reason in r["reasons"]:
                log(f"           reason: {reason}")
        print(human_summary(cfg, records, dup_groups, True, time.time() - t0))
        return 0

    staging.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    # Concurrent runs on one staging folder corrupt each other's temp files — refuse.
    lock = state_dir / "lock"
    if lock.exists():
        try:
            other = int(lock.read_text().strip())
        except (ValueError, OSError):
            other = None
        alive = False
        if other:
            try:
                os.kill(other, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
        if alive:
            log(f"another cdjprep run (pid {other}) is already working on this staging "
                f"folder — refusing to start a second one")
            return 2
    lock.write_text(str(os.getpid()))
    atexit.register(lambda: lock.unlink(missing_ok=True))
    renamed = 0
    for rec in records:
        old = rec.pop("_old_output", None)
        if not old or rec["action"] != "skip" or not rec.get("output") or old == rec["output"]:
            continue
        src_p, dst_p = Path(old), Path(rec["output"])
        try:
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            if src_p.exists() and not dst_p.exists():
                shutil.move(str(src_p), str(dst_p))
                renamed += 1
                if (src_p.parent.resolve() != staging.resolve() and src_p.parent.exists()
                        and not any(src_p.parent.iterdir())):
                    src_p.parent.rmdir()
        except OSError as e:
            rec["warnings"].append(f"rename failed: {e}")
    if renamed:
        log(f"renamed {renamed} existing outputs to the new naming scheme (no re-encoding)")
    tmpdir = state_dir / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    global _done_count
    _done_count = 0
    work = [r for r in new_recs if r["action"] in ("copy", "convert")]

    def do(rec):
        try:
            materialize_file(cfg, rec, tmpdir, len(work))
        except Exception as e:  # noqa: BLE001 — one bad file must not kill the run
            rec["action"] = "reject"
            rec["reasons"].append(f"processing error: {e}")
            for leftover in (Path(rec["output"]),):
                leftover.unlink(missing_ok=True)
            progress(len(work), f"ERROR    {Path(rec['source']).name}  ({e})")
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(do, work))

    # Replace-cleanup (user-authorized): when a source was reprocessed and its output
    # landed under a NEW name, remove the tool's own previous output for that source.
    # Only paths recorded in our manifest, only inside the staging folder.
    staging_resolved = staging.resolve()
    for rec in new_recs:
        if rec["action"] not in ("copy", "convert") or not rec.get("output"):
            continue
        old = stale.get(str(Path(rec["source"]).resolve()))
        if not old or old == rec["output"] or not Path(rec["output"]).exists():
            continue
        op = Path(old)
        try:
            if op.exists() and op.resolve().is_relative_to(staging_resolved):
                op.unlink()
                if op.parent.resolve() != staging_resolved and not any(op.parent.iterdir()):
                    op.parent.rmdir()
        except OSError:
            pass

    for rec in records:
        rec.pop("_art", None)
        rec.pop("_old_output", None)
        p = Path(rec["source"])
        if not p.exists():
            continue
        if rec["action"] == "reject" and any("processing error" in x for x in rec["reasons"]):
            manifest.pop(str(p.resolve()), None)  # transient failure — retry next run
            continue
        st = p.stat()
        manifest[str(p.resolve())] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                                      "config_sig": sig, "convert_sig": csig,
                                      "done_at": time.time(),
                                      "tool_version": TOOL_VERSION, "record": rec}
    state_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    report = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "tool_version": TOOL_VERSION,
              "config_sig": sig, "records": records, "duplicates": dup_groups}
    (state_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    shutil.rmtree(tmpdir, ignore_errors=True)
    print(human_summary(cfg, records, dup_groups, False, time.time() - t0))
    log(f"machine-readable report: {state_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
