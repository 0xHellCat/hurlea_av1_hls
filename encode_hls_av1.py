#!/usr/bin/env python3
import subprocess
import json
import os
import math
import sys
from pathlib import Path

# ----------------- CONFIG -----------------
PRESET = "6"
HLS_TIME = 4
LADDER = {
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "2160p": 2160
}
BITRATES = {
    "480p": "750k",
    "720p": "1500k",
    "1080p": "3500k",
    "1440p": "6000k",
    "2160p": "12000k"
}
AUDIO_BITRATE = "128k"
INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
# ------------------------------------------

def run(cmd, check=True):
    print("▶", " ".join(cmd))
    return subprocess.run(cmd, check=check)

def ffprobe_streams(input_file):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_file]
    p = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(p.stdout)["streams"]

def get_video_resolution(streams):
    for s in streams:
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise RuntimeError("Aucune piste vidéo trouvée")

def sanitize(text):
    if not text:
        return ""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)

def extract_all_subs(input_file, out_sub_dir, streams):
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    extracted = []

    for i, s in enumerate(subs):
        lang = sanitize(s.get("tags", {}).get("language", ""))
        title = sanitize(s.get("tags", {}).get("title", ""))

        base = f"sub_{i}"
        if lang:
            base += f"_{lang}"
        if title:
            base += f"_{title}"

        out_vtt = out_sub_dir / f"{base}.vtt"

        cmd = [
            "ffmpeg", "-y", "-i", str(input_file),
            "-map", f"0:s:{i}",
            "-c:s", "webvtt",
            str(out_vtt)
        ]
        run(cmd)
        extracted.append({
            "index": i,
            "path": out_vtt,
            "lang": lang or "und",
            "name": title or base
        })
    return extracted

def generate_audio_playlists(input_file, audio_root, streams):
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    results = []

    for i, s in enumerate(audios):
        lang = sanitize(s.get("tags", {}).get("language", ""))
        title = sanitize(s.get("tags", {}).get("title", ""))

        base = f"audio_{i}"
        if lang:
            base += f"_{lang}"

        audio_dir = audio_root / base
        audio_dir.mkdir(parents=True, exist_ok=True)

        playlist_path = str(audio_dir / f"{base}.m3u8")
        segment_pattern = str(audio_dir / f"{base}_%03d.m4s")

        cmd = [
            "ffmpeg", "-y", "-i", str(input_file),
            "-map", f"0:a:{i}",
            "-c:a", "aac",
            "-b:a", AUDIO_BITRATE,
            "-vn",
            "-f", "hls",
            "-hls_time", str(HLS_TIME),
            "-hls_playlist_type", "vod",
            "-hls_segment_type", "fmp4",
            "-hls_segment_filename", str(segment_pattern),
            str(playlist_path)
        ]
        run(cmd)

        results.append({
            "index": i,
            "playlist": playlist_path,
            "group_id": "audio",
            "name": title or base,
            "lang": lang or "und"
        })

    return results

def compute_scaled_width(src_w, src_h, target_h):
    w = int(round((target_h * src_w) / src_h))
    return w + (w % 2)

def encode_video_quality(input_file, out_dir, label, target_h, bitrate, src_w, src_h):
    out_dir.mkdir(parents=True, exist_ok=True)

    playlist = str(out_dir / f"{label}.m3u8")
    segment_pat = str(out_dir / f"{label}_%03d.m4s")
    scaled_w = compute_scaled_width(src_w, src_h, target_h)

    cmd = [
        "ffmpeg", "-y", "-i", str(input_file),
        "-map", "0:v:0",
        "-vf", f"scale={scaled_w}:{target_h}",
        "-c:v", "libsvtav1",
        "-preset", PRESET,
        "-b:v", bitrate,
        "-g", "48", "-keyint_min", "48",
        "-an",
        "-f", "hls",
        "-hls_time", str(HLS_TIME),
        "-hls_playlist_type", "vod",
        "-hls_segment_type", "fmp4",
        "-hls_segment_filename", str(segment_pat),
        str(playlist)
    ]

    run(cmd)

    return {
        "label": label,
        "playlist": playlist,
        "bandwidth": int(bitrate.replace("k","")) * 1000,
        "resolution": f"{scaled_w}x{target_h}"
    }

def append_master_video(master, entry):
    with open(master, "a") as f:
        f.write(
            f'#EXT-X-STREAM-INF:BANDWIDTH={entry["bandwidth"]},'
            f'RESOLUTION={entry["resolution"]},AUDIO="audio",SUBTITLES="subs"\n'
        )
        rel = os.path.relpath(entry["playlist"], start=master.parent)
        f.write(f"{rel}\n\n")

def write_master_header(master_path, audio_entries, subtitle_entries):
    with open(master_path, "w") as m:
        m.write("#EXTM3U\n\n")

        for a in audio_entries:
            rel = os.path.relpath(a["playlist"], start=master_path.parent)
            default = "YES" if a["index"] == 0 else "NO"
            m.write(
                f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="{a["name"]}",'
                f'LANGUAGE="{a["lang"]}",DEFAULT={default},AUTOSELECT={default},URI="{rel}"\n'
            )
        m.write("\n")

        for s in subtitle_entries:
            rel = os.path.relpath(s["path"], start=master_path.parent)
            m.write(
                f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{s["name"]}",'
                f'LANGUAGE="{s["lang"]}",DEFAULT=NO,AUTOSELECT=NO,FORCED=NO,URI="{rel}"\n'
            )
        m.write("\n")

def main():
    input_files = list(INPUT_DIR.glob("*"))
    if not input_files:
        print("Aucun fichier trouvé dans ./input/")
        return

    for file in input_files:
        print(f"\n=== Traitement : {file} ===")

        base = file.stem
        output_root = OUTPUT_DIR / base
        video_root = output_root / "video"
        audio_root = output_root / "audio"
        subs_root = output_root / "subtitles"

        for d in [video_root, audio_root, subs_root]:
            d.mkdir(parents=True, exist_ok=True)

        streams = ffprobe_streams(str(file))
        src_w, src_h = get_video_resolution(streams)
        print("Résolution source :", src_w, "x", src_h)

        # subtitles
        subs = extract_all_subs(file, subs_root, streams)

        # audio (each in separate folder)
        audio_entries = generate_audio_playlists(file, audio_root, streams)

        master_path = output_root / "master.m3u8"
        write_master_header(master_path, audio_entries, subs)

        for label, h in sorted(LADDER.items(), key=lambda x: x[1]):
            if h > src_h:
                continue
            out_dir = video_root / label
            entry = encode_video_quality(file, out_dir, label, h, BITRATES[label], src_w, src_h)
            append_master_video(master_path, entry)
            print(f"{label} ajouté au master.")

        print(f"\n✔ Terminé : {output_root}")

if __name__ == "__main__":
    main()
