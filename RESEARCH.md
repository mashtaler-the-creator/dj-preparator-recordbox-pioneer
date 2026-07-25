# RESEARCH.md — CDJ-900 (2010) library prep: findings and decisions

Research date: 2026-07-25. Compiled from three parallel research passes (hardware limits,
audio engineering practice, rekordbox behavior). Every decision in the tool references a
decision ID from this file (`D1`…`D17`). Confidence labels: **[official]** = vendor
documentation, **[secondary]** = forums/community tools/blogs, **[inference]** = reasoned
from official facts.

The primary hardware source is the official CDJ-900 Operating Instructions
(DRB1486-B, 32 pp.), fetched from AlphaTheta's own server:
<https://downloads.support.alphatheta.com/manuals/dj-players/CDJ-900/CDJ-900_DRB1486B_manual.pdf>
Page numbers below refer to this PDF. The primary rekordbox source is the rekordbox 7
Instruction Manual: <https://cdn.rekordbox.com/files/20240509141437/rekordbox7.0.0_manual_EN.pdf>.

---

## 1. CDJ-900 (original, 2010) — what it plays

Manual p.8: *"Music files (MP3/AAC/WAV/AIFF) recorded on USB devices can be played."*
These four formats only. **No FLAC, no ALAC** — confirmed as a hardware (not firmware)
limitation by Pioneer staff on the official forum
([thread](https://forums.pioneerdj.com/hc/en-us/community/posts/203089269--SOLVED-Flac-lossless-format-support-),
[secondary]); FLAC arrived only with CDJ-2000NXS2/XDJ-1000MK2.

| Format | Accepted variants (manual p.8) **[official]** |
|---|---|
| MP3 | MPEG-1 L3: 32/44.1/48 kHz, 32–320 kbps. MPEG-2 L3: 16/22.05/24 kHz, 16–160 kbps. CBR and VBR both play; VBR makes search/super-fast-search slower — Pioneer recommends CBR. Ext `.mp3`. ID3 v1.0/1.1/2.2/2.3/2.4. |
| AAC | **MPEG-4 AAC LC only** (no HE-AAC). 16–48 kHz, 16–320 kbps. Ext `.m4a`/`.aac`/`.mp4`. DRM-protected files explicitly rejected. |
| WAV | **16- or 24-bit uncompressed integer PCM only**, **44.1/48 kHz only**. Ext `.wav`. Track info read from RIFF LIST/INFO chunk. |
| AIFF | **16- or 24-bit uncompressed PCM only**, **44.1/48 kHz only**. Ext `.aif`/`.aiff`. |

### USB storage (manual p.8, p.30) **[official]**
- Filesystems: **FAT, FAT32, HFS+**. No exFAT, no NTFS.
- Folder nesting: **max 8 levels** — deeper files cannot be played.
- Max 10,000 files per folder / 10,000 folders per folder (display limit).
- Multi-partition devices "may not be recognized" — single partition; MBR +
  first-partition-only is community best practice, not documented spec **[secondary]**
  ([CDM guide](https://cdm.link/how-to-avoid-usb-and-rekordbox-djing-failures-a-complete-guide/)).
- USB port supplies 5 V / 500 mA max (p.30).

### Known-rejected encoding variants
| Variant | Status | Source |
|---|---|---|
| 32-bit float WAV | Rejected (spec allows int 16/24 only) | manual p.8 **[official by exclusion]**; E-8305 reports **[secondary]** |
| WAVE_FORMAT_EXTENSIBLE (fmt tag 0xFFFE) | **Rejected with E-8305 even when payload is plain 16/24-bit PCM.** Undocumented by Pioneer; classic Bandcamp-WAV trap. **Implementation note (verified locally 2026-07-25): ffprobe resolves the EXTENSIBLE GUID and reports codec_tag 0x0001, hiding the header — detection must parse the fmt chunk bytes directly. ffmpeg itself writes EXTENSIBLE headers for 24-bit WAV output, so 24-bit WAVs are very commonly affected.** | [Pioneer forum](https://forums.pioneerdj.com/hc/en-us/community/posts/360000447183-Pioneer-XDJ-1000-unsupported-file-format-Bandcamp-wav-encoding-issue), [elektronauts](https://www.elektronauts.com/t/pioneer-usb-decks-solved-an-issue-with-wavs-not-being-recognised/199489), fixer tool [pioneer-wav-fixer](https://github.com/7olstoy/pioneer-wav-fixer) **[secondary, widely reproduced]** |
| AIFF-C (compressed AIFF) | Rejected — spec says uncompressed PCM only | manual p.8 **[official by exclusion]** |
| DRM AAC (.m4p / iTunes purchases) | Rejected, explicitly documented | manual p.8 + troubleshooting p.27 **[official]** |
| 88.2/96/176.4/192 kHz WAV/AIFF | Rejected — only 44.1/48 listed | manual p.8 **[official by exclusion]** |
| MPEG-2.5 MP3 (8/11.025/12 kHz) | Rejected — not among listed rates | manual p.8 **[official by exclusion]** |
| HE-AAC / AAC+ | Rejected — AAC LC only | manual p.8 **[official by exclusion]** |
| Mono files | **Unconfirmed either way** — no channel restriction in the manual, no citable failure report. Treated as playable but flagged in reports. | **[inference]** |

### Error codes (manual p.28) **[official]**
E-8304 DECODE ERROR and E-8305 DATA FORMAT ERROR = "Music files that cannot be played
normally are loaded. Format is wrong → replace with proper-format files." E-8306 NO FILE =
library entry whose file was deleted. (Forum wording "E-8305 UNSUPPORTED FILE FORMAT"
maps to the manual's E-8305 entry.)

### Screen / tags (manual p.8, p.11, p.14, p.19, p.28) **[official]**
- Display shows title / artist / album / track number / time / BPM (p.11).
- **"Up to 63 characters can be displayed for each item. The characters that can be
  displayed are letters A to Z, numbers 0 to 9 and certain symbols. Any other characters
  are displayed as '?'."** (p.11) → Cyrillic, kana, and even accented Latin render as `?`.
- With a rekordbox-exported database, browse categories come from rekordbox (artist,
  album, BPM, rating, genre, label, remixer, original artist, playlist…) — p.14, p.19, p.28.
  **Key is not shown anywhere on the original CDJ-900** (absent from its display/sort
  docs — key display is a nexus-generation feature) **[inference from official manual]**.
- Without a database the player falls back to folder browsing reading file tags; no
  search, no sort (p.14, p.19).
- Path length: no official CDJ-900 limit documented. AlphaTheta support documents that
  **paths over 256 characters break playback via PRO DJ LINK / rekordbox**
  ([support article](https://support.pioneerdj.com/hc/en-us/articles/4408365824153-Some-audio-files-won-t-play-rekordbox) **[official]**).

---

## 2. rekordbox — import, analysis, auto gain

### Formats rekordbox accepts (rekordbox 7 manual pp.244–245) **[official]**
MP3, AAC (.m4a/.mp4), WAV (uncompressed PCM 16/24-bit, 44.1–192 kHz), AIFF (same),
ALAC, FLAC (16/24-bit, 44.1–192 kHz). **32-bit float WAV is outside the spec** and fails
in practice **[official by omission + secondary]**. AIFF-C not listed → unsupported.
DRM files "may not be readable" (p.244).

### Tags rekordbox reads (manual p.13) **[official]**
ID3 v1–v2.4 of MP3 **and AIFF**, MP4 meta atoms of M4A, RIFF INFO of WAVE, Vorbis
Comments of FLAC. Warning in the manual: WAV tag info "may not be displayed" — WAV is
the weak tag format; **AIFF carries full ID3 and is the reliable lossless choice**.
- rekordbox does its own key analysis and can write it back to the ID3 TKEY tag
  (manual p.224) **[official]**; it reads TKEY on import **[secondary]**.
- **TBPM does not seed the analyzer** — analyzed BPM comes from rekordbox's beatgrid
  and overrides tag BPM ([forum](https://forums.pioneerdj.com/hc/en-us/community/posts/115017891783-Rekordbox-screwing-up-the-analyzed-BPM) **[secondary; no official statement either way]**).
- **Neither rekordbox nor CDJs read ReplayGain tags.** rekordbox Auto Gain measures
  loudness during analysis and stores the gain in its own database; it applies only in
  rekordbox Performance mode. **CDJs have no auto-gain at all — exported tracks play at
  the loudness baked into the file.**
  ([zenn.dev deep dive](https://zenn.dev/jphfa/articles/67ae6fc23f4561?locale=en) **[secondary, technically detailed]**;
  Auto Gain as analysis product: manual p.117/p.135/p.228 **[official]**).

### BPM analysis at 160–180 BPM
- Two modes: **[Normal]** ("relatively consistent tempo") and **[Dynamic]** ("significant
  tempo changes"); **[BPM Range] applies to Normal mode only** (manual p.14, p.224)
  **[official]**. For electronically-produced constant-tempo music use **Normal** —
  Dynamic risks spurious tempo-change markers on syncopated breaks **[secondary]**
  ([Lexicon blog](https://www.lexicondj.com/blog/understanding-rekordbox-beatgrid-analysis)).
- **Half-tempo detection is the classic DnB failure** (175 → 87.5). Fix: set BPM Range
  high enough before analysis; **98–195 is the range cited for DnB**
  ([Lexicon](https://www.lexicondj.com/blog/understanding-rekordbox-beatgrid-analysis) **[secondary]**).
  The exact dropdown enumeration in current rekordbox 7 could not be verified from
  official sources — **no consensus/unconfirmed**; pick the range whose upper bound
  clears 180. Grid editor has ×2/÷2 BPM buttons (manual p.135) **[official]**.
- Breakbeat grids may still drift on syncopated sections; manual regridding +
  **Analysis Lock** (manual p.92) protects corrected grids **[official]**.

### Artwork
Pioneer CDJ artwork rule: **JPEG, max 800×800, embedded**
([bliss summary of Pioneer docs](https://www.blisshq.com/music-library-management-blog/2020/03/03/pioneer-cdj-album-art/) **[secondary]**).
Oversized/PNG art is a confirmed *display* failure, not an import failure. No official
byte cap found — **unconfirmed**.

### ID3 version
rekordbox reads v1–v2.4 **[official]**; Serato officially recommends **ID3v2.3** and its
corrupt-file fix rewrites tags as v2.3
([Serato support](https://support.serato.com/hc/en-us/articles/202552460-How-to-Strip-and-Convert-ID3-tags) **[official Serato]**);
Windows shell reads only up to v2.3. → **Normalize to ID3v2.3 (UTF-16)** as the best
common denominator **[inference]**.

### Exotic filenames
FAT32 long filenames are UTF-16, so Cyrillic/emoji are storable **[inference]** — but
rekordbox 6.8.5 users report export failures ("File cannot be found") for tracks whose
metadata contains special characters
([report](https://community.pioneerdj.com/hc/en-us/community/posts/32034443124121) **[secondary]**),
and emoji behavior is untested anywhere — treat as high-risk, strip/transliterate.
FAT32 illegal characters: `< > : " / \ | ? *`, control chars 0x00–0x1F, no trailing
space/dot, reserved names CON/PRN/AUX/NUL/COM1–9/LPT1–9, 255 chars per name component
([Microsoft](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file) **[official]**).
FAT32 file size cap 4 GiB **[well-established]**.

---

## 3. Audio engineering practice

### Dither on 24→16 reduction
- "Always dither when reducing bit depth" is the professional norm — Ian Shepherd
  ([when-to-dither](https://productionadvice.co.uk/when-to-dither/) **[authoritative]**);
  physics per xiph.org: dithered quantization adds only uncorrelated noise
  ([Monty](https://people.xiph.org/~xiphmont/demo/neil-young.html) **[authoritative]**).
- The counter-position (inaudible on loud club masters) is also well supported
  ([Waves](https://www.waves.com/audio-dithering-what-you-need-to-know) **[community consensus on physics]**) —
  **contested only on audibility, not on harm**. Dither is free and harmless → **always dither**.
- Type: flat TPDF is the universally-defensible choice (Paul Frindle via Shepherd);
  noise-shaped (shibata) buys a few dB but should only ever be the *last* operation.
  ffmpeg offers `triangular`, `triangular_hp`, `lipshitz`, `shibata`, …
  ([ffmpeg resampler docs](https://ffmpeg.org/ffmpeg-resampler.html) **[official ffmpeg]**).

### Resampling quality
- soxr at high precision is the measurably-best option in ffmpeg
  ([src.infinitewave.ca](https://src.infinitewave.ca) canon; summaries:
  [archimago](http://archimago.blogspot.com/2019/06/guest-post-why-we-should-use-software.html))
  **[community consensus backed by measurement]**; audible difference vs swr on real
  music is near nil — the win is measurable correctness, not audibility.
- **Local constraint (verified 2026-07-25): the installed Homebrew ffmpeg 8.1.2 is built
  WITHOUT libsoxr** (`aresample=resampler=soxr` fails at filter init). Fallback: swr with
  enlarged filter (`filter_size=256`) approaches soxr's stopband performance; or install
  `sox` for true soxr. Given that resampling applies only to non-44.1/48 kHz sources
  (rare in this library), **swr HQ is the default**; installing sox is offered as an option.

### Loudness
- Club/festival masters commonly land at **−8…−6 LUFS integrated**
  ([weaponsounds](https://www.weaponsounds.com/blogs/production-tips/mastering-techno-club-vs-streaming-lufs),
  [sage audio](https://www.sageaudio.com/articles/mastering-for-dance-music) **[community consensus; no formal standard exists]**).
- Since CDJs have no auto-gain and ignore ReplayGain (see §2), tags cannot level anything
  at the booth; the DJ community consensus remains "use the trim knob." Destructive
  re-leveling of purchased masters is contested and risky.
  → **Measure LUFS-I and true peak, write them into tags for information, report
  outliers, never alter gain by default.** Opt-in pure static gain (attenuation only,
  no limiter) exists for hot tracks. **[contested area; conservative default chosen]**

### True peak / intersample clipping
- Measure with `ebur128=peak=true` (BS.1770 4× oversampled) or `loudnorm=print_format=json`
  (`input_tp`) ([ffmpeg filters docs](https://ffmpeg.org/ffmpeg-filters.html) **[official ffmpeg]**).
- **−1 dBTP** is the industry-standard ceiling
  ([Shepherd](https://productionadvice.co.uk/spotify-upload-true-peak/) **[authoritative]**);
  club-only masters sometimes accept −0.3…−0.1 **[contested]**. Intersample overs are
  physically real (DAC reconstruction clipping, up to ~3 dB over sample peak —
  [measurement](https://mnaganov.github.io/2017/05/dac-clipping-on-intersample-peaks.html) **[authoritative]**),
  but virtually every commercial DnB release exceeds 0 dBTP and plays in clubs nightly.
  → **Warn above the ceiling; never auto-attenuate by default; never limit.**

### Silence at track start
Pioneer's own docs: Auto Cue "skips the silent section below −60 dB and sets the cue
immediately before the sound starts"
([Pioneer memory-cue PDF](https://www.pioneerdj.com/-/media/pioneerdj/downloads/firmwares/systems/xdj-r1/xdj-r1_memory_cue_auto_cue_load_e.pdf) **[official]**).
Beatgrids anchor to onsets, not file start. Trimming risks chopping quiet atmospheric
jungle intros and shifts any file-position-based cues.
→ **Never trim. Warn if leading silence > 2 s** (informational).

### DC offset
Legacy problem (analog chains, old converters); modern digital releases essentially never
ship meaningful DC **[community consensus]**. Detect via `astats` DC offset field
**[official ffmpeg]**. A blanket highpass on sub-heavy jungle is riskier than the disease
(phase shift near the sub band). → **Detect and warn only (|DC| > 0.001); never auto-fix.**

### BPM detection (external, pre-rekordbox)
- The half/double octave error is the documented classic failure on DnB (170 detected as
  85) — MIR literature ([tempo estimation survey](https://arxiv.org/pdf/2107.09208))
  **[authoritative]**. Mitigation: constrain/bias the range; for a 160–180 library,
  any sub-100 detection is a half-tempo error with near certainty → double it.
- Tooling on this machine (Python 3.14 rules out librosa/essentia wheels for now):
  **aubio CLI** (brew) is fast and adequate with the octave-correction heuristic applied.
  **[no consensus benchmark exists across tools for DnB — stated explicitly]**
- rekordbox ignores TBPM for analysis (§2), so the written TBPM serves the DJ's eyes and
  non-Pioneer software; rekordbox will re-derive BPM itself.

### Key detection
- libkeyfinder (Ibrahim Shaath's algorithm, maintained by the Mixxx team) is designed for
  electronic music ([libkeyfinder](https://github.com/mixxxdj/libkeyfinder) **[authoritative provenance]**);
  no ready Homebrew CLI formula exists (verified 2026-07-25) — `keyfinder-cli` must be
  built from source against brew's `libkeyfinder`.
- Key detection on harmonically sparse jungle is inherently low-confidence — **consensus**.
- rekordbox performs its own key analysis regardless and reads/writes TKEY (§2);
  the original CDJ-900 cannot display key at all (§1).

### Duplicate detection
- Approaches: (a) tag match — cheap, misses mistags, false-positives on VIP/remix names;
  (b) exact audio-stream hash (hash decoded PCM, not the file, so tag edits don't hide
  duplicates) — zero false positives, misses re-encodes; (c) Chromaprint acoustic
  fingerprint — catches same-recording-different-encode
  ([chromaprint](https://acoustid.org/chromaprint) **[official]**; AcoustID treats >7 s
  duration difference as different recordings
  ([MusicBrainz](https://musicbrainz.org/doc/Guides/AcoustID) **[official]**)).
- Chroma features ignore timbre → a VIP with identical arrangement can false-positive;
  in DnB, title tokens (VIP/remix/edit/bootleg) must veto auto-grouping **[inference]**.
- Prior art: beets `duplicates` plugin (MBIDs + optional `ffmpeg -f crc` stream checksum);
  rekordbox 7's own "Merge Duplicate Files" is pure Title+Artist matching; Lexicon DJ
  uses fingerprint + tag dual method and archives instead of deleting
  ([Lexicon manual](https://www.lexicondj.com/manual/find-duplicates) **[official Lexicon]**).
→ **Layered: (1) exact PCM hash = certain duplicate; (2) Chromaprint similarity with
  ≤7 s duration gate = probable, review; (3) normalized tag match with VIP/remix token
  awareness = version family, listed only. Report only — never delete.** (User requirement
  and Lexicon's archive-and-review pattern.)

---

## 4. Decisions (referenced from code as D-numbers)

| ID | Decision | Basis |
|---|---|---|
| **D1** | Conversion target: **AIFF 16-bit / 44.1 kHz** (config `target.bit_depth`, `target.sample_rate`). AIFF over WAV because AIFF carries full ID3 (rekordbox manual p.13) while WAV RIFF-INFO "may not be displayed"; 48 kHz sources stay 48 kHz (playable, no needless resample). 24-bit AIFF is also CDJ-legal — 16 is the user's spec (smaller USB footprint) and is dithered properly. | §1, §2 |
| **D2** | Copy-as-is (no re-encode): MP3 (MPEG-1/2 L3 within rate/bitrate spec), AAC-LC non-DRM (.m4a/.mp4/.aac), WAV int 16/24 @44.1/48 with plain PCM fmt header (0x0001), AIFF uncompressed 16/24 @44.1/48. Everything else → convert per D1. Generation loss is never introduced on playable files. | §1 |
| **D3** | WAVE_FORMAT_EXTENSIBLE (fmt 0xFFFE) WAVs are treated as NOT playable even when payload is 16/24-bit PCM → converted (also fixes the header). | §1 rejected-variants |
| **D4** | Files failing a full decode (`ffmpeg -v error -f null`) or with `.part` extension: excluded. `.part` silently, corrupt with report entry. Full decode of every source file is mandatory. | user req + §2 failure causes |
| **D5** | Resampler: swr with `filter_size=256` + explicit dither (soxr unavailable in local ffmpeg build; difference near-inaudible; resampling rare). Optional: install sox → config `resampler: sox`. | §3 resampling |
| **D6** | Dither on any bit-depth reduction (24→16, float→16): `dither_method=triangular_hp` default; `shibata` available in config. No dither when bit depth does not decrease. | §3 dither |
| **D7** | Loudness: measure LUFS-I/LRA/true-peak per track (ebur128), write `TXXX:LUFS` + ReplayGain-style tags for non-Pioneer tools, report outliers vs −8…−6 LUFS club norm. **No gain change by default**; `--apply-gain` = opt-in static attenuation only. | §2 auto-gain, §3 loudness |
| **D8** | True peak: warn when > −1.0 dBTP (config `true_peak_warn_db`). Never auto-attenuate, never limit. | §3 true peak |
| **D9** | Leading silence: warn if > 2 s; never trim. DC offset: warn if |DC| > 0.001; never fix. | §3 silence/DC |
| **D10** | Tags: normalize to **ID3v2.3** on MP3 and AIFF; map from FLAC Vorbis comments on conversion. Critical fields: artist, title (flag if empty after best effort). Fields written: title, artist, album, track#, year, genre, TBPM, TKEY (if available), TXXX loudness. | §2 tags, §3 |
| **D11** | Transliteration: CDJ-900 renders only `A–Z 0–9` + basic symbols; **anything else → `?`** (official). Non-ASCII in displayed tag fields and in filenames is transliterated to ASCII (Cyrillic → Latin per GOST-ish mapping); originals preserved in `TXXX:ORIG TITLE`/`ORIG ARTIST`. | §1 screen |
| **D12** | Filenames: template (config, default `{artist} - {title}`), ASCII-only after transliteration, FAT32-illegal chars stripped, ≤255 chars/component, full path warned at >200 and errored at >255 (PRO DJ LINK 256 limit), output nesting ≤ 2 levels (≪ 8 CDJ limit). | §1 USB, §2 filenames |
| **D13** | Artwork: embedded art re-encoded to JPEG ≤800×800 during conversion; oversized/PNG art in copied files flagged (not modified — copy means copy). | §2 artwork |
| **D14** | BPM: aubio CLI; detections < `bpm.min_expected` (default 100) doubled (octave-error correction for a 160–180 library); written to TBPM; report flags low-confidence. rekordbox re-analyzes anyway (TBPM not a seed). | §3 BPM |
| **D15** | Duplicates: 3 layers — exact PCM-stream MD5 (from the mandatory full decode, free), Chromaprint similarity (fpcalc, duration gate 7 s, threshold 0.95 configurable), normalized tag match with VIP/remix/edit/bootleg token veto. Report-only, never delete, never auto-skip. | §3 duplicates |
| **D16** | Idempotency: manifest JSON inside staging (`.cdjprep/manifest.json`) keyed by source path+size+mtime → unchanged sources skipped on re-run; `--force` overrides. | user req |
| **D17** | Mono files: copied/converted as-is (playability unconfirmed but no failure evidence); flagged in report as "mono — verify on deck". | §1 mono |

## 5. Explicitly unconfirmed / no-consensus items

1. Mono playback on CDJ-900 — no citable source either way (D17 = flag, don't touch).
2. MBR/first-partition requirement — community lore, manual only says "may not be recognized".
3. Exact rekordbox 7 BPM Range dropdown values — official manual confirms the setting,
   not the enumeration; 98–195 cited for DnB (secondary). Checklist tells the user to
   pick the range covering 160–180.
4. WAVE_FORMAT_EXTENSIBLE rejection — never officially documented, but reproduced across
   many independent reports + a dedicated fixer tool exists; treated as fact (D3).
5. Whether TBPM seeds rekordbox analysis — secondary consensus: it does not.
6. Emoji filenames through rekordbox export — untested anywhere; stripped per D11/D12.
7. Dither audibility on loud club masters — contested; dither applied anyway (harmless, D6).
8. Destructive loudness normalization — genuinely contested in the DJ community;
   conservative default = measure-and-report (D7).
9. BPM-tool accuracy benchmark on DnB — no rigorous cross-tool benchmark exists;
   aubio + octave heuristic chosen for zero-friction availability (D14).
