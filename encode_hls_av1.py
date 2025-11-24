#!/usr/bin/env python3

import sys
from pathlib import Path, PurePath

# ----------------- CONFIG -----------------
PRESET = "13"
HLS_TIME = 4
LADDER = {
    "480p": 854,
    "720p": 1280,
    "1080p": 1920,
    "1440p": 2560,
    "2160p": 3840
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

import subprocess
import json
import os
import shlex
import re
import time

# Si un fichier est passé en argument → on encode seulement celui-là
if len(sys.argv) > 1:
    input_files = [Path(sys.argv[1])]
else:
    input_files = list(INPUT_DIR.glob("*"))

# ------------------------------------------

# ---------- COULEURS & HELPERS ----------
# Palette ANSI (confirmée)
CLR_RESET  = "\033[0m"
CLR_OK     = "\033[92m"   # vert vif
CLR_INFO   = "\033[94m"   # bleu clair
CLR_WARN   = "\033[93m"   # jaune
CLR_ERR    = "\033[91m"   # rouge
CLR_BLOCK  = "\033[96m"   # cyan pour blocs remplis
CLR_EMPTY  = "\033[90m"   # gris pour blocs vides
CLR_PCT    = "\033[97m"   # blanc pour pourcentage
CLR_ETA    = "\033[95m"   # magenta pour ETA
CLR_SPEED  = "\033[92m"   # vert pour vitesse

# largeur d'alignement des labels (pour logs alignés)
LOG_LABEL_WIDTH = 30  # ajuste si tu veux plus court/long

def log_info(label, msg):
    lbl = f"{CLR_INFO}▶ {label.ljust(LOG_LABEL_WIDTH)}{CLR_RESET}"
    print(f"{lbl} {msg}")

def log_ok(label, msg):
    lbl = f"{CLR_OK}✔ {label.ljust(LOG_LABEL_WIDTH)}{CLR_RESET}"
    print(f"{lbl} {msg}")

def log_warn(label, msg):
    lbl = f"{CLR_WARN}⚠ {label.ljust(LOG_LABEL_WIDTH)}{CLR_RESET}"
    print(f"{lbl} {msg}")

def log_err(label, msg):
    lbl = f"{CLR_ERR}✖ {label.ljust(LOG_LABEL_WIDTH)}{CLR_RESET}"
    print(f"{lbl} {msg}")

def hms(seconds):
    seconds = int(round(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"
# ----------------------------------------

def progress_bar_40(pct):
    """Barre 40 blocs : 1 bloc = 2.5%"""
    bars = int(pct / 2.5)  # 100 / 2.5 = 40
    if bars < 0: bars = 0
    if bars > 40: bars = 40
    filled = CLR_BLOCK + "█" * bars + CLR_RESET
    empty  = CLR_EMPTY + "░" * (40 - bars) + CLR_RESET
    return f"{filled}{empty}"

def run_ffmpeg_with_progress(cmd, total_duration, label):
    """
    Exécute ffmpeg avec -progress pipe:1 en affichant :
     - % (white)
     - barre 40 blocs (cyan/gray)
     - elapsed / total (HH:MM:SS)
     - ETA (HH:MM:SS, magenta)
     - speed (x.xx, vert)
    Label is left-aligned to LOG_LABEL_WIDTH.
    Raises CalledProcessError on non-zero return.
    """
    # safe pretty print of command
    pretty = shlex.join([str(x) for x in cmd])
    log_info(label, f"{pretty}")

    # start process
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    start = time.time()
    current_time = 0.0
    last_print = 0.0

    # parse lines like: out_time_ms=1234567
    out_time_re = re.compile(r"out_time_ms=(\d+)")
    for raw in proc.stdout:
        line = raw.strip()
        m = out_time_re.match(line)
        if m:
            current_time = int(m.group(1)) / 1_000_000.0

            if total_duration and total_duration > 0:
                pct = min(100.0, (current_time / total_duration) * 100.0)
            else:
                pct = 0.0

            elapsed = time.time() - start
            speed = (current_time / elapsed) if elapsed > 0 else 0.0
            remaining = ((total_duration - current_time) / speed) if (total_duration and speed > 0) else 0.0
            if remaining < 0: remaining = 0.0

            # throttle prints so terminal isn't overwhelmed (e.g. 5x / sec)
            if time.time() - last_print > 0.2:
                bar = progress_bar_40(pct)
                pct_str = f"{CLR_PCT}{pct:5.1f}%{CLR_RESET}"
                elapsed_str = hms(current_time)
                total_str = hms(total_duration) if total_duration else "??:??:??"
                eta_str = hms(remaining)
                speed_str = f"{CLR_SPEED}{speed:.2f}x{CLR_RESET}"

                #Aligned label
                lbl = f"{label.ljust(LOG_LABEL_WIDTH)}"
                sys.stdout.write(
                    f"\r{CLR_INFO}{lbl}{CLR_RESET} "
                    f"{pct_str} {bar}  "
                    f"{elapsed_str} / {total_str}  {CLR_ETA}ETA:{eta_str}{CLR_RESET}  {speed_str}"
                )
                sys.stdout.flush()
                last_print = time.time()

    proc.wait()
    # ensure newline after progress
    print()

    if proc.returncode != 0:
        log_err(label, f"ffmpeg failed (code {proc.returncode})")
        # print last output block for debug
        raise subprocess.CalledProcessError(proc.returncode, cmd)

    log_ok(label, "terminé.")
    return True


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

def select_audio_codec(stream):
    """
    Retourne le codec ffmpeg à utiliser selon le codec source.
    Compatible HLS :
        - AAC / AC3 / EAC3 → copy
    Incompatible :
        - TrueHD → EAC3 7.1
        - DTS / DTS-HD → EAC3 5.1
    """
    codec = stream.get("codec_name", "").lower()

    # --- Direct copy (aucune perte) ---
    if codec in ("aac", "ac3", "eac3"):
        return {
            "codec": "copy",
            "bitrate": None
        }

    # --- TrueHD → Atmos core → EAC3 7.1 ---
    if codec == "truehd":
        return {
            "codec": "eac3",
            "bitrate": "1536k"   # EXCELLENT pour garder l’Atmos core
        }

    # --- DTS & DTS-HD → EAC3 5.1 ---
    if codec in ("dts", "dts_hd_ma", "dts-ma", "dts-hd"):
        return {
            "codec": "eac3",
            "bitrate": "896k"
        }

    # Fallback sécurisé
    return {
        "codec": "eac3",
        "bitrate": "640k"
    }

def sanitize(text):
    if not text:
        return ""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)

def extract_all_subs(input_file, out_sub_dir, streams):
    """
    Extrait toutes les pistes subtitle texte en VTT.
    Utilise run_ffmpeg_with_progress pour afficher la progression.
    Skip bitmap (PGS/DVD).
    """
    input_file = Path(input_file)
    out_sub_dir = Path(out_sub_dir)
    out_sub_dir.mkdir(parents=True, exist_ok=True)

    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    extracted = []

    UNSUPPORTED = {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub", "dvb_subtitle"}

    # obtenir la durée totale (pour calcul du %) - fallback None si échec
    try:
        total_duration = get_duration(str(input_file)) or 0.0
    except:
        total_duration = 0.0

    for s in subs:
        real_index = s.get("index")
        codec = s.get("codec_name", "")
        lang = s.get("tags", {}).get("language", "")
        title = s.get("tags", {}).get("title", "")

        label_base = f"Sous-titre #{real_index}"
        if lang:
            label_base += f" ({lang})"
        if title:
            label_base += f" {title}"

        if codec in UNSUPPORTED:
            log_warn(label_base, f"{codec} ignoré (bitmap non convertible)")
            continue

        # safe filename
        fname = f"sub_{real_index}"
        if lang:
            fname += f"_{sanitize(lang)}"
        if title:
            fname += f"_{sanitize(title)}"

        tmp_srt = out_sub_dir / f"{fname}.srt"
        out_vtt = out_sub_dir / f"{fname}.vtt"

        # 1) Tentative COPY
        cmd_copy = [
            "ffmpeg", "-progress", "pipe:1", "-y",
            "-i", str(input_file),
            "-map", f"0:{real_index}",
            "-c:s", "copy",
            str(tmp_srt)
        ]
        try:
            run_ffmpeg_with_progress(cmd_copy, total_duration, label=f"{label_base} (copy)")
        except subprocess.CalledProcessError:
            log_warn(label_base, "copy échoué, tentative forced...")

        # check file existence and size
        if not tmp_srt.exists() or tmp_srt.stat().st_size == 0:
            # 2) forced extraction to srt
            log_info(label_base, "extraction forcée en srt...")
            cmd_force = [
                "ffmpeg", "-progress", "pipe:1", "-y",
                "-i", str(input_file),
                "-map", f"0:{real_index}",
                "-c:s", "srt",
                str(tmp_srt)
            ]
            try:
                run_ffmpeg_with_progress(cmd_force, total_duration, label=f"{label_base} (forced)")
            except subprocess.CalledProcessError:
                log_err(label_base, "extraction forcée échouée → skip")
                if tmp_srt.exists():
                    try: tmp_srt.unlink()
                    except: pass
                continue

        # 3) Convert SRT -> VTT (we can show progress using duration of file)
        try:
            run_ffmpeg_with_progress(
                ["ffmpeg", "-progress", "pipe:1", "-y", "-i", str(tmp_srt), str(out_vtt)],
                total_duration,
                label=f"{label_base} (VTT)"
            )
        except subprocess.CalledProcessError:
            log_err(label_base, "conversion VTT échouée → skip")
            if tmp_srt.exists():
                try: tmp_srt.unlink()
                except: pass
            continue

        # cleanup srt
        if tmp_srt.exists():
            try: tmp_srt.unlink()
            except: pass

        extracted.append({
            "index": real_index,
            "path": str(out_vtt),
            "lang": lang or "",
            "name": title or fname
        })
        log_ok(label_base, f"extrait -> {out_vtt.name}")

    return extracted


def generate_audio_playlists(input_file, audio_root, streams):
    # streams est une liste brute -> on filtre les pistes audio
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    entries = []

    for s in audio_streams:
        idx = s["index"]
        lang = s.get("tags", {}).get("language", "und")

        out_dir = audio_root / f"audio_{idx}_{lang}"
        out_dir.mkdir(parents=True, exist_ok=True)

        out_m3u8 = out_dir / f"audio_{idx}_{lang}.m3u8"
        seg_pattern = out_dir / f"audio_{idx}_{lang}_%03d.m4s"

        print(f"\n▶ Audio piste #{idx} ({lang}) : extraction...")

        # Déterminer le codec à utiliser selon la source
        audio_config = select_audio_codec(s)
        codec = audio_config["codec"]
        bitrate = audio_config.get("bitrate", AUDIO_BITRATE)
        source_codec = s.get("codec_name", "").lower()

        # obtenir la durée totale pour la barre de progression
        total_duration = get_duration(input_file) or 0.0

        # Fonction helper pour construire la commande
        def build_cmd(audio_codec, audio_bitrate=None):
            cmd = [
                "ffmpeg", "-progress", "pipe:1", "-y",
                "-i", input_file,
                "-map", f"0:{idx}",
                "-c:a", audio_codec,
            ]
            if audio_bitrate:
                cmd.extend(["-b:a", audio_bitrate])
            cmd.extend([
                "-vn",
                "-f", "hls",
                "-hls_time", "4",
                "-hls_playlist_type", "vod",
                "-hls_segment_type", "fmp4",
                "-hls_segment_filename", str(seg_pattern),
                str(out_m3u8)
            ])
            return cmd

        # Stratégie : essayer "copy" d'abord si possible, sinon ré-encoder
        success = False
        
        if codec == "copy":
            # Essayer d'abord avec copy (meilleure qualité, aucune perte)
            log_info(f"Audio #{idx}", f"tentative copy ({source_codec})...")
            cmd = build_cmd("copy")
            
            try:
                run_ffmpeg_with_progress(cmd, total_duration, f"Audio #{idx} (copy)")
                success = True
            except subprocess.CalledProcessError:
                log_warn(f"Audio #{idx}", "copy échoué, ré-encodage en AAC...")
                # Si copy échoue, ré-encoder en AAC (compatible HLS fmp4)
                codec = "aac"
                bitrate = AUDIO_BITRATE
        
        if not success:
            # Ré-encoder avec le codec approprié
            if codec == "eac3":
                log_info(f"Audio #{idx}", f"encodage EAC3 {bitrate}...")
            else:
                log_info(f"Audio #{idx}", f"encodage AAC {bitrate}...")
            
            cmd = build_cmd(codec, bitrate)
            
            try:
                run_ffmpeg_with_progress(cmd, total_duration, f"Audio #{idx}")
                success = True
            except subprocess.CalledProcessError:
                log_err(f"Audio #{idx}", "échec encodage → skip")
                continue
        
        if not success:
            log_err(f"Audio #{idx}", "échec → skip")
            continue

        print(f"✔ Audio piste #{idx} → {out_m3u8.name}")

        entries.append({
            "type": "audio",
            "index": idx,
            "lang": lang,
            "playlist": out_m3u8,
            "name": f"Audio {lang}" if lang != "und" else f"Audio {idx}"
        })

    return entries



def compute_scaled_size_from_width(src_w, src_h, target_w):
    """
    Calcule les dimensions redimensionnées en conservant le ratio d'aspect.
    target_w est la largeur cible.
    """
    if target_w >= src_w:
        return src_w, src_h
    ratio = target_w / src_w
    scaled_w = target_w
    scaled_h = int(src_h * ratio)
    # S'assurer que scaled_h est pair (requis par certains codecs)
    if scaled_h % 2 != 0:
        scaled_h += 1
    return scaled_w, scaled_h

def encode_video_quality(input_file, out_dir, label, target_w, bitrate, src_w, src_h):
    """
    Encode une qualité video-only AV1 dans out_dir.
    Retourne un dict avec playlist + bandwidth + resolution.
    target_w est la largeur cible (LADDER contient des largeurs).
    """
    input_file = Path(input_file)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    playlist = out_dir / f"{label}.m3u8"
    segment_pat = out_dir / f"{label}_%03d.m4s"
    scaled_w, scaled_h = compute_scaled_size_from_width(src_w, src_h, target_w)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_file),
        "-map", "0:v:0",
        "-vf", f"scale={scaled_w}:{scaled_h}",
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
    duration = get_duration(str(input_file)) or 0.0

    cmd_progress = cmd.copy()
    cmd_progress.insert(1, "-progress")
    cmd_progress.insert(2, "pipe:1")

    try:
        run_ffmpeg_with_progress(cmd_progress, duration, label=f"Vidéo {label}")
    except subprocess.CalledProcessError:
        log_err(f"Vidéo {label}", "échec encodage → skip")
        raise

    return {
        "label": label,
        "playlist": str(playlist),
        "bandwidth": int(bitrate.replace("k", "")) * 1000,
        "resolution": f"{scaled_w}x{scaled_h}"
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
        for label, target_w in sorted(LADDER.items(), key=lambda x: x[1]):
            if target_w > src_w:
                continue
            out_dir = video_root / label
            try:
                entry = encode_video_quality(str(file), out_dir, label, target_w, BITRATES[label], src_w, src_h)
            except Exception:
                print(f"⚠️ Encodage {label} échoué, on continue")
                continue
            append_master_video(master_path, entry)
            print(f"✅ {label} ajouté au master.")

        print(f"\n✔ Terminé : {output_root}")

if __name__ == "__main__":
    main()
