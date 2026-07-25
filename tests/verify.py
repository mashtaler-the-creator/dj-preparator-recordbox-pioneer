#!/usr/bin/env python3
"""Run cdjprep against the synthetic trap set and verify every expectation.
Exit code 0 only if every check passes. Prints expected-vs-actual line by line."""

import array
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from mutagen.aiff import AIFF

HERE = Path(__file__).parent
TOOL = HERE.parent / "cdjprep.py"
CFG = HERE / "config-test.toml"
SRC = HERE / "src"
STAGING = HERE / "staging"
FF = "/opt/homebrew/bin/ffmpeg"
FP = "/opt/homebrew/bin/ffprobe"

results = []


def check(name, ok, expected, actual):
    results.append((name, bool(ok), expected, actual))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}\n         expected: {expected}\n         actual:   {actual}")


def run_tool(*args):
    r = subprocess.run([str(TOOL), "--config", str(CFG), *args],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        sys.exit(f"tool exited {r.returncode}")
    return r.stdout


def decode_s16(path):
    r = subprocess.run([FF, "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
                        "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", "-f", "s16le", "-"],
                       stdin=subprocess.DEVNULL, capture_output=True)
    a = array.array("h")
    a.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    return a


def probe(path):
    r = subprocess.run([FP, "-v", "error", "-print_format", "json",
                        "-show_format", "-show_streams", str(path)],
                       stdin=subprocess.DEVNULL, capture_output=True)
    return json.loads(r.stdout)


def header4(path, off=8):
    with open(path, "rb") as f:
        f.seek(off)
        return f.read(4)


def main():
    if STAGING.exists():
        shutil.rmtree(STAGING)

    print("== step 1: --dry-run on clean staging (must write nothing) ==")
    out = run_tool("--dry-run")
    check("dry-run creates no staging dir", not STAGING.exists(),
          "staging absent", "absent" if not STAGING.exists() else "EXISTS")
    check("dry-run announces itself", "DRY RUN" in out, "'DRY RUN' in output", "found" if "DRY RUN" in out else "missing")

    print("\n== step 2: real run ==")
    run_tool()
    report = json.loads((STAGING / ".cdjprep" / "report.json").read_text())
    recs = {Path(r["source"]).name: r for r in report["records"]}

    def rec(name):
        return recs.get(name)

    def expect_action(name, action, reason_sub):
        r = rec(name)
        if r is None:
            check(f"{name}: present in report", False, "record exists", "MISSING")
            return None
        ok = r["action"] == action and any(reason_sub.lower() in x.lower() for x in r["reasons"])
        check(f"{name}: {action} ({reason_sub})", ok,
              f"{action} / reason contains '{reason_sub}'",
              f"{r['action']} / {r['reasons']}")
        return r

    def expect_warn(name, sub):
        r = rec(name)
        ok = r and any(sub.lower() in w.lower() for w in r["warnings"])
        check(f"{name}: warning '{sub}'", ok, f"warning contains '{sub}'",
              str(r["warnings"]) if r else "MISSING")

    r01 = expect_action("01. FLAC16 Track.flac", "convert", "FLAC not playable")
    r02 = expect_action("02. Hires FLAC Track.flac", "convert", "FLAC not playable")
    expect_action("03. ALAC Track.m4a", "convert", "ALAC not playable")
    r04 = expect_action("04. WAV96 Track.wav", "convert", "44.1/48 kHz only")
    r05 = expect_action("05. Float WAV Track.wav", "convert", "float")
    expect_action("06. Extensible Track.wav", "convert", "EXTENSIBLE")
    expect_action("07. AIFC Track.aiff", "convert", "AIFF-C")
    expect_action("08. Plain AIFF Track.aiff", "copy", "within spec")
    expect_action("09. MP3 320 Track.mp3", "copy", "within CDJ-900 spec")
    expect_action("10. Mono Track.flac", "convert", "FLAC not playable")
    expect_warn("10. Mono Track.flac", "mono")
    expect_action("11. DC Offset Track.flac", "convert", "FLAC not playable")
    expect_warn("11. DC Offset Track.flac", "DC offset")
    r12 = expect_action("12. Clipped Track.wav", "copy", "within spec")
    expect_warn("12. Clipped Track.wav", "clipping")
    r13 = rec("13. Truncated Track.flac")
    ok13 = r13 and r13["action"] == "reject" and any(
        ("truncat" in x.lower() or "decode" in x.lower()) for x in r13["reasons"])
    check("13. Truncated Track.flac: reject (full decode catches it)", ok13,
          "reject / truncated or decode error",
          f"{r13['action']} / {r13['reasons']}" if r13 else "MISSING")
    r14 = expect_action("14. Test Artist - Pathtag Track.flac", "convert", "FLAC")
    if r14:
        check("14: tags recovered from path", r14["tags_final"]["artist"] == "Test Artist"
              and r14["tags_final"]["title"] == "Pathtag Track"
              and r14["tags_final"]["album"] == "Trap Album",
              "artist/title/album from folder+filename",
              str({k: r14['tags_final'][k] for k in ('artist', 'title', 'album')}))
    expect_warn("orphan.flac", "empty critical tag")
    rcy = expect_action("01. Кириллица - Трек 😎.flac", "convert", "FLAC")
    expect_action("Deep Track.flac", "convert", "FLAC")
    expect_action("18. Duplicate Track.flac", "convert", "FLAC")
    expect_action("18b. Duplicate Track.mp3", "copy", "MP3")
    expect_action("20. DRM Track.m4p", "reject", "DRM")
    check(".part skipped silently",
          not any(".part" in n for n in recs),
          "no .part in report", [n for n in recs if ".part" in n] or "none")

    print("\n== step 3: output files ==")
    if r01:
        out01 = Path(r01["output"])
        check("01 output is true FORM/AIFF (not AIFC)", header4(out01) == b"AIFF",
              "AIFF", header4(out01))
        st = probe(out01)["streams"][0]
        check("01 output codec/rate", st["codec_name"] == "pcm_s16be"
              and st["sample_rate"] == "44100",
              "pcm_s16be @ 44100", f"{st['codec_name']} @ {st['sample_rate']}")
        a, b = decode_s16(SRC / "Test Artist - Trap Album (2024)/01. FLAC16 Track.flac"), \
            decode_s16(out01)
        check("01 PCM bit-identical after 16->16 conversion (no dither applied)",
              a == b, "identical samples", f"equal={a == b} len {len(a)} vs {len(b)}")
        tags = AIFF(out01).tags
        check("01 ID3 written v2.3", tags is not None and tags.version[:2] == (2, 3),
              "ID3v2.3", str(tags.version if tags else None))
        apic = tags.getall("APIC") if tags else []
        dims_ok = False
        if apic:
            art = HERE / "_art_check.jpg"
            art.write_bytes(apic[0].data)
            stp = probe(art)["streams"][0]
            dims_ok = (apic[0].mime == "image/jpeg" and stp["width"] <= 800
                       and stp["height"] <= 800)
            check("01 artwork JPEG <=800x800", dims_ok, "jpeg <=800",
                  f"{apic[0].mime if apic else '-'} {stp['width']}x{stp['height']}" if apic else "no APIC")
            art.unlink()
        else:
            check("01 artwork JPEG <=800x800", False, "APIC present", "no APIC")
        an = r01.get("analysis", {})
        check("01 BPM detected and tagged", bool(an.get("bpm")) and "TBPM" in tags,
              "bpm value + TBPM frame", f"bpm={an.get('bpm')} TBPM={'TBPM' in tags}")
        print(f"         (info) 01 detected BPM = {an.get('bpm')} (signal built at 174)")
        check("01 key detected", bool(an.get("key")), "TKEY value", str(an.get("key")))
        check("01 loudness measured", an.get("lufs_i") is not None and
              an.get("true_peak_db") is not None,
              "LUFS-I + true peak", f"{an.get('lufs_i')} LUFS / {an.get('true_peak_db')} dBTP")
    if r02:
        st = probe(r02["output"])["streams"][0]
        check("02 hires resampled to 44.1/16", st["codec_name"] == "pcm_s16be"
              and st["sample_rate"] == "44100",
              "pcm_s16be @ 44100", f"{st['codec_name']} @ {st['sample_rate']}")
        check("02 dither step recorded", any("dither" in s for s in r02["steps"]),
              "dither in steps", str(r02["steps"]))
        sd = float(probe(SRC / "Test Artist - Trap Album (2024)/02. Hires FLAC Track.flac")["format"]["duration"])
        dd = float(probe(r02["output"])["format"]["duration"])
        check("02 duration preserved", abs(sd - dd) < 0.2, f"{sd:.2f}s ±0.2", f"{dd:.2f}s")
    if r04:
        check("04 resample step recorded", any("resample 96000->44100" in s for s in r04["steps"]),
              "resample 96000->44100", str(r04["steps"]))
    if r05:
        a = decode_s16(SRC / "Test Artist - Trap Album (2024)/05. Float WAV Track.wav")
        b = decode_s16(r05["output"])
        n = min(len(a), len(b))
        maxdiff = max(abs(a[i] - b[i]) for i in range(0, n, 7)) if n else 99999
        check("05 float->16 dither noise <= 2 LSB", len(a) == len(b) and maxdiff <= 2,
              "max |diff| <= 2 LSB, same length", f"max diff {maxdiff}, len {len(a)} vs {len(b)}")
    if r12:
        tp = r12.get("analysis", {}).get("true_peak_db")
        check("12 true peak measured > 0 dBTP", tp is not None and tp > 0,
              "> 0 dBTP", str(tp))
    if rcy:
        out = Path(rcy["output"])
        check("cyrillic filename transliterated ASCII", out.name.isascii()
              and "Kirillitsa" in out.name and "Trek" in out.name,
              "ASCII name with Kirillitsa/Trek", out.name)
        tags = AIFF(out).tags
        tit = str(tags["TIT2"].text[0]) if tags and "TIT2" in tags else ""
        origs = {f.desc: str(f.text[0]) for f in tags.getall("TXXX")} if tags else {}
        check("cyrillic tags transliterated + originals kept",
              tit == "Trek" and origs.get("ORIG TITLE") == "Трек 😎",
              "TIT2='Trek', ORIG TITLE='Трек 😎'", f"TIT2={tit!r} ORIG={origs.get('ORIG TITLE')!r}")
    deep_rec = rec("Deep Track.flac")
    if deep_rec:
        depth = deep_rec["output_rel"].count("/") + 1
        check("deep-nested input flattened to <=2 levels", depth <= 2,
              "<= 2 levels in staging", f"{depth} ({deep_rec['output_rel']})")
    for name, r in recs.items():
        if r["action"] in ("copy", "convert") and r.get("output"):
            if not Path(r["output"]).exists():
                check(f"{name}: output exists", False, "file exists", "MISSING")
    rejected_in_staging = [r for r in report["records"] if r["action"] == "reject"
                           and r.get("output") and Path(r["output"]).exists()]
    check("no rejected file reached staging", not rejected_in_staging, "none",
          str(rejected_in_staging) or "none")
    bad = [str(p.relative_to(STAGING)) for p in STAGING.rglob("*")
           if p.is_file() and not p.name.startswith(".")
           and ".cdjprep" not in p.parts
           and (not p.relative_to(STAGING).as_posix().isascii()
                or re.search(r'[<>:"\\|?*]', p.name))]
    check("all staging paths FAT32-safe ASCII", not bad, "none", bad or "none")

    print("\n== step 4: duplicates ==")
    dups = report["duplicates"]
    flat = [(g["kind"], tuple(sorted(Path(f).name for f in g["files"]))) for g in dups]
    dup_pair = [k for k, fs in flat
                if fs == ("18. Duplicate Track.flac", "18b. Duplicate Track.mp3")]
    check("FLAC/MP3 same-audio pair flagged", any("duplicate" in k for k in dup_pair),
          "probable/possible duplicate", str(dup_pair) or str(flat))
    fam_pair = [k for k, fs in flat
                if fs == ("19. Family Track.flac", "19b. Family Track (VIP).flac")]
    check("VIP pair classified as version family (not duplicate)",
          any("version family" in k for k in fam_pair) or
          all("duplicate" not in k for k in fam_pair),
          "version family / no duplicate verdict", str(fam_pair) or "no group (acceptable)")
    wrong = [g for g in dups
             if "version family" not in g["kind"]
             and len({Path(f).name.split(".")[0].rstrip("b") for f in g["files"]}) > 1
             and tuple(sorted(Path(f).name for f in g["files"])) != ("18. Duplicate Track.flac", "18b. Duplicate Track.mp3")]
    check("no false-positive duplicate groups", not wrong, "none",
          json.dumps(wrong, ensure_ascii=False) if wrong else "none")

    print("\n== step 5: idempotency ==")
    mtimes = {p: p.stat().st_mtime_ns for p in STAGING.rglob("*")
              if p.is_file() and ".cdjprep" not in p.parts}
    out2 = run_tool()
    report2 = json.loads((STAGING / ".cdjprep" / "report.json").read_text())
    acts = {r["action"] for r in report2["records"]}
    check("second run: everything skip/reject, nothing reprocessed",
          acts <= {"skip", "reject"}, "only skip+reject", str(acts))
    mtimes2 = {p: p.stat().st_mtime_ns for p in STAGING.rglob("*")
               if p.is_file() and ".cdjprep" not in p.parts}
    check("second run: no output file rewritten", mtimes == mtimes2,
          "identical mtimes", "identical" if mtimes == mtimes2 else "CHANGED")

    print("\n" + "=" * 64)
    fails = [r for r in results if not r[1]]
    print(f"RESULT: {len(results) - len(fails)}/{len(results)} checks passed")
    if fails:
        print("FAILED CHECKS:")
        for name, _, exp, act in fails:
            print(f"  - {name}: expected {exp}, got {act}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
