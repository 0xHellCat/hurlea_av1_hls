#!/usr/bin/env python3
import subprocess
import json
import os
import sys
import shlex
import re
import time
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
    """
    Exécute la commande (liste d'arguments). Affiche la commande (sécurisée),
    capture stdout/stderr et soulève une exception si erreur.
    """
    # Affichage lisible et sûr de la commande
    pretty = shlex.join([str(x) for x in cmd])
    print("▶", pretty)

    result = subprocess.run([str(x) for x in cmd],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True)
    if result.returncode != 0:
        print("❌ FFmpeg/commande error (returncode={}):".format(result.returncode))
        # Affiche stderr (FFmpeg)
        if result.stderr:
            print(result.stderr)
        # Affiche stdout si utile
        if result.stdout:
            print(result.stdout)
        raise subprocess.CalledProcessError(result.returncode, cmd)
    return result

def get_duration(input_file):
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nk=1:nw=1",
        input_file
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return float(out.decode().strip())
    except:
        return None

def ffprobe_streams(input_file):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(input_file)]
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
    """
    Extrait toutes les pistes subtitle texte en VTT.
    - utilise les index réels (s['index'])
    - skip les pistes bitmap (PGS/DVD)
    - tente copy, si échoue force extraction en srt, puis convertit srt->vtt
    """
    input_file = Path(input_file)
    out_sub_dir = Path(out_sub_dir)
    out_sub_dir.mkdir(parents=True, exist_ok=True)

    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    extracted = []

    UNSUPPORTED = {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub", "dvb_subtitle"}

    for pos, s in enumerate(subs):
        real_index = s.get("index")
        codec = s.get("codec_name", "")
        lang = s.get("tags", {}).get("language", "")
        title = s.get("tags", {}).get("title", "")

        if codec in UNSUPPORTED:
            print(f"⚠️  Sous-titre #{real_index} ({codec}) ignoré (bitmap non convertible)")
            continue

        fname = f"sub_{real_index}"
        if lang:
            fname += f"_{sanitize(lang)}"
        if title:
            fname += f"_{sanitize(title)}"

        tmp_srt = out_sub_dir / f"{fname}.srt"
        out_vtt = out_sub_dir / f"{fname}.vtt"

        # 1) Tentative copy
        cmd_copy = [
            "ffmpeg", "-y",
            "-i", str(input_file),
            "-map", f"0:{real_index}",
            "-c:s", "copy",
            str(tmp_srt)
        ]

        ok = True
        try:
            run(cmd_copy)
        except Exception:
            ok = False

        # 2) Si copy échoue ou fichier vide -> extraction forcée en SRT
        if not ok or not tmp_srt.exists() or tmp_srt.stat().st_size == 0:
            if tmp_srt.exists():
                try:
                    tmp_srt.unlink()
                except Exception:
                    pass
            print(f"⚠️  Copy impossible sur #{real_index}, tentative extraction forcée en SRT...")
            cmd_force = [
                "ffmpeg", "-y",
                "-i", str(input_file),
                "-map", f"0:{real_index}",
                "-c:s", "srt",
                str(tmp_srt)
            ]
            try:
                run(cmd_force)
            except Exception:
                print(f"❌ Impossible d'extraire piste #{real_index} → skip")
                if tmp_srt.exists():
                    try:
                        tmp_srt.unlink()
                    except Exception:
                        pass
                continue

        # 3) Convertir SRT -> VTT
        cmd_vtt = [
            "ffmpeg", "-y",
            "-i", str(tmp_srt),
            str(out_vtt)
        ]
        try:
            run(cmd_vtt)
        except Exception:
            print(f"❌ Conversion en VTT impossible pour #{real_index} → skip")
            if out_vtt.exists():
                try:
                    out_vtt.unlink()
                except Exception:
                    pass
            continue
        finally:
            if tmp_srt.exists():
                try:
                    tmp_srt.unlink()
                except Exception:
                    pass

        extracted.append({
            "index": real_index,
            "path": str(out_vtt),
            "lang": lang or "",
            "name": title or fname
        })
        print(f"✅ Sous-titre #{real_index} extrait -> {out_vtt.name}")

    return extracted

def run_progress(cmd, duration, label=""):
    print(f"\n▶ {label} : démarrage...\n")
    pretty = shlex.join([str(x) for x in cmd])
    print("CMD:", pretty)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1
    )

    start_time = time.time()
    current_time = 0

    time_re = re.compile(r"out_time_ms=(\d+)")

    for line in process.stdout:
        line = line.strip()

        m = time_re.match(line)
        if m:
            current_time = int(m.group(1)) / 1_000_000

            if duration and duration > 0:
                pct = (current_time / duration) * 100
                elapsed = time.time() - start_time
                speed = current_time / elapsed if elapsed > 0 else 1
                remaining = (duration - current_time) / speed if speed > 0 else 0

                sys.stdout.write(
                    f"\r[{label}] {pct:5.1f}%  "
                    f"({int(current_time)}/{int(duration)} sec)  "
                    f"ETA: {int(remaining)} sec  "
                )
                sys.stdout.flush()

    process.wait()
    print()

    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd)

    print(f"✔ {label} terminé.\n")

def generate_audio_playlists(input_file, audio_root, streams):
    """
    Crée une playlist HLS (audio-only fMP4) par piste audio.
    Utilise l'index réel de chaque piste audio.
    """
    input_file = Path(input_file)
    audio_root = Path(audio_root)
    audio_root.mkdir(parents=True, exist_ok=True)

    audios = [s for s in streams if s.get("codec_type") == "audio"]
    results = []

    for a in audios:
        real_index = a.get("index")
        lang = sanitize(a.get("tags", {}).get("language", ""))
        title = sanitize(a.get("tags", {}).get("title", ""))

        base = f"audio_{real_index}"
        if lang:
            base += f"_{lang}"

        audio_dir = audio_root / base
        audio_dir.mkdir(parents=True, exist_ok=True)

        playlist_path = audio_dir / f"{base}.m3u8"
        segment_pattern = audio_dir / f"{base}_%03d.m4s"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_file),
            "-map", f"0:{real_index}",
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
        try:
            duration = get_duration(input_file)

            cmd_progress = cmd.copy()
            cmd_progress.insert(1, "-progress")
            cmd_progress.insert(2, "pipe:1")

            run_progress(cmd_progress, duration, label=f"Audio piste #{real_index}")


        except Exception:
            print(f"❌ Échec encodage audio piste #{real_index} → skip")
            continue

        results.append({
            "index": real_index,
            "playlist": str(playlist_path),
            "group_id": "audio",
            "name": title or base,
            "lang": lang or "und"
        })
        print(f"✅ Audio piste #{real_index} -> {playlist_path.name}")

    return results

def compute_scaled_width(src_w, src_h, target_h):
    w = int(round((target_h * src_w) / src_h))
    return w + (w % 2)

def encode_video_quality(input_file, out_dir, label, target_h, bitrate, src_w, src_h):
    """
    Encode une qualité video-only AV1 dans out_dir.
    Retourne un dict avec playlist + bandwidth + resolution.
    """
    input_file = Path(input_file)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    playlist = out_dir / f"{label}.m3u8"
    segment_pat = out_dir / f"{label}_%03d.m4s"
    scaled_w = compute_scaled_width(src_w, src_h, target_h)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_file),
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

    try:
        duration = get_duration(input_file)

        cmd_progress = cmd.copy()
        cmd_progress.insert(1, "-progress")
        cmd_progress.insert(2, "pipe:1")

        run_progress(cmd_progress, duration, label=f"Video {label}")


    except Exception:
        print(f"❌ Échec encodage video {label} → skip")
        raise

    return {
        "label": label,
        "playlist": str(playlist),
        "bandwidth": int(bitrate.replace("k", "")) * 1000,
        "resolution": f"{scaled_w}x{target_h}"
    }

def append_master_video(master_path, entry):
    """
    Ajoute une entrée EXT-X-STREAM-INF au master.
    """
    master_path = Path(master_path)
    with open(master_path, "a", encoding="utf-8") as f:
        f.write(
            f'#EXT-X-STREAM-INF:BANDWIDTH={entry["bandwidth"]},'
            f'RESOLUTION={entry["resolution"]},AUDIO="audio",SUBTITLES="subs"\n'
        )
        rel = os.path.relpath(entry["playlist"], start=str(master_path.parent))
        f.write(f"{rel}\n\n")

def write_master_header(master_path, audio_entries, subtitle_entries):
    master_path = Path(master_path)
    master_path.parent.mkdir(parents=True, exist_ok=True)
    with open(master_path, "w", encoding="utf-8") as m:
        m.write("#EXTM3U\n\n")
        # audio tracks
        for idx, a in enumerate(audio_entries):
            rel = os.path.relpath(a["playlist"], start=str(master_path.parent))
            default = "YES" if idx == 0 else "NO"
            autoselect = "YES" if idx == 0 else "NO"
            m.write(
                f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="{a["name"]}",'
                f'LANGUAGE="{a["lang"]}",DEFAULT={default},AUTOSELECT={autoselect},URI="{rel}"\n'
            )
        m.write("\n")
        # subtitles
        for s in subtitle_entries:
            rel = os.path.relpath(s["path"], start=str(master_path.parent))
            m.write(
                f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{s["name"]}",'
                f'LANGUAGE="{s["lang"]}",DEFAULT=NO,AUTOSELECT=NO,FORCED=NO,URI="{rel}"\n'
            )
        m.write("\n")

def main():
    input_files = sorted([p for p in INPUT_DIR.iterdir() if p.is_file()])
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

        # 1) subtitles
        print("\n--- Extraction sous-titres ---")
        subs = extract_all_subs(str(file), subs_root, streams)

        # 2) audio
        print("\n--- Encodage audio / packaging ---")
        audio_entries = generate_audio_playlists(str(file), audio_root, streams)

        # 3) master header (audio + subtitles)
        master_path = output_root / "master.m3u8"
        write_master_header(master_path, audio_entries, subs)
        print(f"\nMaster initial créé : {master_path}")

        # 4) encode video qualities and append to master progressively
        print("\n--- Encodage vidéos ---")
        for label, h in sorted(LADDER.items(), key=lambda x: x[1]):
            if h > src_h:
                continue
            out_dir = video_root / label
            try:
                entry = encode_video_quality(str(file), out_dir, label, h, BITRATES[label], src_w, src_h)
            except Exception:
                print(f"⚠️ Encodage {label} échoué, on continue")
                continue
            append_master_video(master_path, entry)
            print(f"✅ {label} ajouté au master.")

        print(f"\n✔ Terminé : {output_root}")

if __name__ == "__main__":
    main()
