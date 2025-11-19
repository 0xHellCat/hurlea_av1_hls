#!/usr/bin/env python3
import subprocess
import json
import os
import math
import sys
from pathlib import Path

# ----------------- CONFIG -----------------
PRESET = "6"               # libsvtav1 preset (0 meilleur/plus lent ... 13 plus rapide)
HLS_TIME = 4               # durée des segments en secondes
LADDER = {                 # label -> target_height
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "2160p": 2160
}
BITRATES = {               # label -> bitrate string (k suffix)
    "480p": "750k",
    "720p": "1500k",
    "1080p": "3500k",
    "1440p": "6000k",
    "2160p": "12000k"
}
AUDIO_BITRATE = "128k"
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
            return int(s.get("width")), int(s.get("height"))
    raise RuntimeError("Aucune piste vidéo trouvée")

def sanitize_name(base, idx, lang, codec_type):
    # create a filename-friendly string
    parts = [base, str(idx)]
    if lang:
        parts.append(lang)
    return "_".join(parts)

def ensure_dirs(path: Path):
    path.mkdir(parents=True, exist_ok=True)

def extract_all_subs(input_file, out_sub_dir, streams):
    """
    Extrait chaque piste subtitle en WebVTT (un fichier .vtt par piste).
    """
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    extracted = []
    for i, s in enumerate(subs):
        lang = s.get("tags", {}).get("language") if s.get("tags") else None
        name = s.get("tags", {}).get("title") if s.get("tags") else None
        fname = f"sub_{i}"
        if lang:
            fname += f"_{lang}"
        if name:
            # sanitize name: replace spaces
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
            fname += f"_{safe}"
        out_vtt = out_sub_dir / f"{fname}.vtt"
        # ffmpeg: map subtitle stream, convert to webvtt (one file)
        cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-map", f"0:s:{i}",
            "-c:s", "webvtt",
            str(out_vtt)
        ]
        run(cmd)
        extracted.append({
            "index": i,
            "path": out_vtt,
            "lang": lang or "",
            "name": name or fname
        })
    return extracted

def generate_audio_playlists(input_file, out_audio_dir, streams):
    """
    Pour chaque piste audio on crée une playlist HLS fMP4 (audio-only).
    Retourne liste d'objets {index, uri, group_id, name, lang}
    """
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    results = []
    for i, s in enumerate(audios):
        lang = s.get("tags", {}).get("language") if s.get("tags") else ""
        title = s.get("tags", {}).get("title") if s.get("tags") else ""
        # create filename friendly
        fname = f"audio_{i}"
        if lang:
            fname += f"_{lang}"
        out_dir = out_audio_dir
        ensure_dirs(out_dir)
        playlist_path = out_dir / f"{fname}.m3u8"
        segment_pattern = out_dir / f"{fname}_%03d.m4s"

        cmd = [
            "ffmpeg", "-y", "-i", input_file,
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
            "name": title or f"audio_{i}",
            "lang": lang or ""
        })
    return results

def compute_scaled_width(src_w, src_h, target_h):
    w = int(round((target_h * src_w) / src_h))
    # make even
    if w % 2 == 1:
        w += 1
    return w

def encode_video_quality(input_file, out_video_quality_dir, label, target_h, bitrate, src_w, src_h):
    ensure_dirs(out_video_quality_dir)
    playlist = out_video_quality_dir / f"{label}.m3u8"
    segment_pat = out_video_quality_dir / f"{label}_%03d.m4s"
    scaled_w = compute_scaled_width(src_w, src_h, target_h)
    # encode video-only (no audio), AV1 libsvtav1
    cmd = [
        "ffmpeg", "-y", "-i", input_file,
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

def append_master_video_entry(master_path: Path, video_entry):
    """
    Ajoute l'entrée vidéo au master (EXT-X-STREAM-INF). On suppose que les EXT-X-MEDIA
    (audio & subtitles) ont déjà été écrits en tête du master.
    """
    with open(master_path, "a") as m:
        m.write(f"#EXT-X-STREAM-INF:BANDWIDTH={video_entry['bandwidth']},RESOLUTION={video_entry['resolution']},AUDIO=\"audio\",SUBTITLES=\"subs\"\n")
        # write relative URI to playlist
        rel = os.path.relpath(video_entry["playlist"], start=master_path.parent)
        m.write(f"{rel}\n\n")

def write_master_header(master_path: Path, base_dir: Path, audio_entries, subtitle_entries):
    """
    Initialise le master.m3u8 avec #EXTM3U et les EXT-X-MEDIA pour les pistes audio et subtitles.
    """
    with open(master_path, "w") as m:
        m.write("#EXTM3U\n\n")
        # audio tracks
        for a in audio_entries:
            rel = os.path.relpath(a["playlist"], start=master_path.parent)
            # DEFAULT=YES for first audio track
            default = "YES" if a["index"] == 0 else "NO"
            # AUTOSELECT=YES for first too
            auto = "YES" if a["index"] == 0 else "NO"
            attrs = (
                f'TYPE=AUDIO,GROUP-ID="audio",NAME="{a["name"]}",LANGUAGE="{a["lang"]}",DEFAULT={default},AUTOSELECT={auto},URI="{rel}"'
            )
            m.write(f"#EXT-X-MEDIA:{attrs}\n")
        m.write("\n")
        # subtitles
        for s in subtitle_entries:
            rel = os.path.relpath(s["path"], start=master_path.parent)
            # HLS requires WEBVTT to be referenced as URI here
            attrs = (
                f'TYPE=SUBTITLES,GROUP-ID="subs",NAME="{s["name"]}",LANGUAGE="{s["lang"]}",DEFAULT=NO,AUTOSELECT=NO,FORCED=NO,URI="{rel}"'
            )
            m.write(f"#EXT-X-MEDIA:{attrs}\n")
        m.write("\n")

def main(input_file):
    input_path = Path(input_file)
    if not input_path.exists():
        print("Fichier introuvable:", input_file); return

    base = input_path.stem
    output_root = Path("output") / base
    audio_dir = output_root / "audio"
    subs_dir = output_root / "subtitles"
    video_root = output_root / "video"
    ensure_dirs(output_root)
    ensure_dirs(audio_dir)
    ensure_dirs(subs_dir)
    ensure_dirs(video_root)

    print("Analyse du fichier...")
    streams = ffprobe_streams(str(input_path))
    src_w, src_h = get_video_resolution(streams)
    print("Résolution source:", src_w, "x", src_h)

    # 1) Extraire sous-titres (un fichier .vtt par piste)
    print("\nExtraction des sous-titres...")
    subtitle_entries = extract_all_subs(str(input_path), subs_dir, streams)
    print("Sous-titres extraits:", [str(s["path"]) for s in subtitle_entries])

    # 2) Générer playlists audio (HLS fMP4) pour chaque piste audio
    print("\nEncodage / packaging audio...")
    audio_entries = generate_audio_playlists(str(input_path), audio_dir, streams)
    print("Audio HLS généré:", [str(a["playlist"]) for a in audio_entries])

    # 3) Créer master.m3u8 initial (avec audio + subtitles entries)
    master_path = output_root / "master.m3u8"
    write_master_header(master_path, output_root, audio_entries, subtitle_entries)
    print("\nMaster initial créé:", master_path)

    # 4) Boucle ladder : encoder chaque qualité disponible (<= source height)
    print("\nEncodage vidéos (AV1) par qualité...")
    # ensure we process ladder in ascending order
    for label, t_h in sorted(LADDER.items(), key=lambda x: int(x[1])):
        if t_h > src_h:
            continue
        out_quality_dir = video_root / label
        print(f"\n--- Encodage {label} ({t_h}p) ---")
        video_entry = encode_video_quality(str(input_path), out_quality_dir, label, t_h, BITRATES[label], src_w, src_h)
        append_master_video_entry(master_path, video_entry)
        print(f"Qualité {label} terminée — master mis à jour.")

    print("\nTerminé. Structure dans :", output_root)
    print("Master playlist :", master_path)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python encode_hls_av1_full.py input_file.mkv")
        sys.exit(1)
    main(sys.argv[1])
