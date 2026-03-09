import argparse
import os
import re
import subprocess
import sys
import time
import wave
from typing import List, Tuple

BOOK_DIR = "frutales-en"
OUTPUT_VIDEO_NAME = None
IMAGE_DURATION_SECONDS: float | None = None

WIDTH = 1920
HEIGHT = 1080
FPS = 24

# Pausa de silencio entre audios al concatenar (en segundos). 0.0 = sin pausa.
AUDIO_GAP_SECONDS = 0.1

# Segundos a recortar del inicio de cada audio de narración (para eliminar ruidos de arranque).
AUDIO_TRIM_START_SECONDS = 0.1

# Volumen relativo de la música de fondo (1.0 = mismo volumen que la narración).
#MUSIC_VOLUME = 0.04
MUSIC_VOLUME = 0.04

# Denoise opcional sobre el audio final (narración +/- música).
# Parámetros suaves para estática leve: nr=reducción (dB), nf=umbral, tn=1 adapta al contenido.
# Ajustar nr: 6=más brillo, 10=más limpieza.
ENABLE_AUDIO_DENOISE = True
AUDIO_DENOISE_FILTER = "afftdn=nr=8:nf=-50:tn=1"

# Efecto visual aplicado a cada imagen antes de las transiciones.
# Valores soportados: "none", "pulse".
IMAGE_EFFECT = "pulse"

# Parámetros del efecto "pulse" (zoom in-out suave).
PULSE_STRENGTH = 0.03  # amplitud del zoom (0.03 = +/-3 %)
PULSE_PERIOD = 10.0     # segundos por ciclo completo de in-out

# Tipo de transición para el primer cambio de imagen (xfade transition=...).
FIRST_TRANSITION = "slideleft"
# Tipo de transición para el resto de cambios de imagen.
OTHER_TRANSITION = "slideright"
# Duración de cada transición en segundos.
TRANSITION_DURATION = 0.8

# Si es False, los WAV intermedios generados solo para el render con FFmpeg
# (por capítulo: *_audio.wav, *_music.wav) se eliminarán automáticamente
# tras generar el MP4. Los WAV usados para --export-resolve siempre se conservan.
KEEP_INTERMEDIATE_AUDIO_FILES = False


def _ffmpeg_has_nvenc() -> bool:
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return False
    try:
        out = subprocess.run(
            [ffmpeg_exe, "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
        return "h264_nvenc" in (out.stdout or "") + (out.stderr or "")
    except Exception:
        return False


def _get_nvidia_gpu_name(gpu_index: int = 0) -> str | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
        if out.returncode != 0 or not out.stdout:
            return None
        lines = [ln.strip() for ln in out.stdout.strip().splitlines() if ln.strip()]
        if gpu_index < len(lines):
            return lines[gpu_index].strip()
        return lines[0].strip() if lines else None
    except Exception:
        return None


def resolve_book_dirs(book_arg: str, base_dir: str | None = None) -> Tuple[str, str, str, str, str]:
    path = os.path.abspath(book_arg)
    if os.path.isdir(path):
        book_dir = path
        book_name = os.path.basename(book_dir)
    else:
        base = os.path.abspath(base_dir or os.getcwd())
        book_name = book_arg
        book_dir = os.path.join(base, book_name)

    audio_dir = os.path.join(book_dir, "audio")
    img_dir = os.path.join(book_dir, "img")
    video_dir = os.path.join(book_dir, "video")

    if not os.path.isdir(audio_dir):
        raise FileNotFoundError(f"No existe la carpeta de audio: {audio_dir}")
    if not os.path.isdir(img_dir):
        raise FileNotFoundError(f"No existe la carpeta de imágenes: {img_dir}")

    os.makedirs(video_dir, exist_ok=True)
    return book_name, book_dir, audio_dir, img_dir, video_dir


def get_audio_duration_seconds(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as wav:
        n = wav.getnframes()
        rate = wav.getframerate()
        return n / float(rate) if rate else 0.0


def _seconds_to_timecode(sec: float, fps: int) -> str:
    total_frames = int(round(sec * fps))
    if total_frames < 0:
        total_frames = 0
    h = total_frames // (3600 * fps)
    remainder = total_frames % (3600 * fps)
    m = remainder // (60 * fps)
    remainder = remainder % (60 * fps)
    s = remainder // fps
    f = remainder % fps
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def concat_audio_to_wav(
    audio_paths: List[str],
    output_wav_path: str,
    trim_start_seconds: float = 0.0,
) -> None:
    if not audio_paths:
        raise ValueError("concat_audio_to_wav: lista de audio vacía")
    nchannels = sampwidth = framerate = None
    with wave.open(output_wav_path, "wb") as out_wav:
        for idx, path in enumerate(audio_paths):
            with wave.open(path, "rb") as inp:
                if nchannels is None:
                    nchannels = inp.getnchannels()
                    sampwidth = inp.getsampwidth()
                    framerate = inp.getframerate()
                    out_wav.setnchannels(nchannels)
                    out_wav.setsampwidth(sampwidth)
                    out_wav.setframerate(framerate)
                else:
                    if (
                        inp.getnchannels() != nchannels
                        or inp.getsampwidth() != sampwidth
                        or inp.getframerate() != framerate
                    ):
                        raise ValueError(
                            f"WAV {path} tiene formato distinto al primero "
                            f"(nchannels/sampwidth/framerate deben coincidir)"
                        )
                # Descartar un pequeño tramo inicial si se ha configurado recorte.
                nframes_total = inp.getnframes()
                trim_frames = 0
                if trim_start_seconds > 0.0 and framerate:
                    trim_frames = int(trim_start_seconds * framerate)
                    if trim_frames > nframes_total:
                        trim_frames = nframes_total
                    if trim_frames > 0:
                        _ = inp.readframes(trim_frames)

                remaining_frames = nframes_total - trim_frames
                if remaining_frames > 0:
                    out_wav.writeframes(inp.readframes(remaining_frames))

            if AUDIO_GAP_SECONDS > 0.0 and idx < len(audio_paths) - 1:
                assert nchannels is not None and sampwidth is not None and framerate is not None
                gap_frames = int(AUDIO_GAP_SECONDS * framerate)
                if gap_frames > 0:
                    silence_frame = b"\x00" * sampwidth * nchannels
                    out_wav.writeframes(silence_frame * gap_frames)


def write_edl(
    entries: List[Tuple[str, float]],
    output_edl_path: str,
    fps: int,
    transition_duration_sec: float,
) -> None:
    transition_frames = max(0, int(round(transition_duration_sec * fps)))
    lines: List[str] = ["TITLE: Export from pdf-to-audio", ""]
    record_sec = 0.0
    for i, (image_path, duration_sec) in enumerate(entries):
        reel = os.path.splitext(os.path.basename(image_path))[0]
        event_num = i + 1
        source_in_tc = "00:00:00:00"
        source_out_sec = duration_sec
        source_out_tc = _seconds_to_timecode(source_out_sec, fps)
        record_in_tc = _seconds_to_timecode(record_sec, fps)
        record_out_sec = record_sec + duration_sec
        record_out_tc = _seconds_to_timecode(record_out_sec, fps)

        if i == 0 or transition_frames == 0:
            # Cut
            lines.append(
                f"{event_num:03d}  {reel:<8} V C  {source_in_tc} {source_out_tc} {record_in_tc} {record_out_tc}"
            )
        else:
            # Dissolve from previous to this
            prev_reel = os.path.splitext(os.path.basename(entries[i - 1][0]))[0]
            lines.append(
                f"{event_num:03d}  {reel:<8} V D {transition_frames:03d} "
                f"{_seconds_to_timecode(record_sec - entries[i - 1][1], fps)} {source_in_tc} "
                f"{record_in_tc} {record_out_tc}"
            )
        record_sec = record_out_sec

    with open(output_edl_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")


def _normalize_path_for_ffmpeg(path: str) -> str:
    return os.path.abspath(path).replace("\\", "/")


def _export_simple_slideshow_with_ffmpeg(
    book_name: str,
    video_dir: str,
    parts: List[Tuple[str, List[str]]],
    duration_per_image: float | None,
    audio_trim_start_seconds: float,
    music_paths: List[str],
    music_volume: float,
    ffmpeg_exe: str,
    use_gpu: bool,
    gpu_index: int,
    preset: str,
) -> None:
    image_entries: List[Tuple[str, float]] = []
    audio_paths_ordered: List[str] = []
    for audio_path, image_paths in parts:
        audio_paths_ordered.append(audio_path)
        if not image_paths:
            continue
        audio_duration = get_audio_duration_seconds(audio_path)
        if audio_trim_start_seconds > 0.0:
            audio_duration = max(0.0, audio_duration - audio_trim_start_seconds)
        n = len(image_paths)
        if duration_per_image is not None:
            dur_per = duration_per_image
        else:
            dur_per = audio_duration / n if n else 0.0
        for img_path in image_paths:
            image_entries.append((img_path, dur_per))

    if not image_entries:
        print("No hay imágenes para generar slideshow simple con FFmpeg.")
        return

    wav_path = os.path.join(video_dir, f"{book_name}_audio.wav")

    concat_audio_to_wav(audio_paths_ordered, wav_path, trim_start_seconds=audio_trim_start_seconds)
    print(f"Audio concatenado: {wav_path}")

    out_name = f"{book_name}.mp4"
    if not out_name.lower().endswith(".mp4"):
        out_name += ".mp4"
    output_path = os.path.join(video_dir, out_name)

    # Preparar pista(s) de música de fondo, si existen.
    music_wav_path: str | None = None
    if music_paths:
        # Por simplicidad, soportamos música en WAV y la concatenamos como con la narración.
        wav_music_paths = [p for p in music_paths if p.lower().endswith(".wav")]
        if wav_music_paths:
            music_wav_path = os.path.join(video_dir, f"{book_name}_music.wav")
            concat_audio_to_wav(wav_music_paths, music_wav_path)
            print(f"Música concatenada: {music_wav_path}")
        else:
            print("ADVERTENCIA: Se encontraron pistas de música pero ninguna en formato WAV; se ignorarán.")

    # Asegurarnos de que las duraciones sean válidas y ajustar TRANSITION_DURATION si hace falta.
    durations = [max(d, 0.01) for _img, d in image_entries]
    if len(durations) >= 2 and TRANSITION_DURATION >= min(durations):
        # Reducir ligeramente para que siempre haya algo de zona sin transición.
        effective_transition = max(min(durations) * 0.4, 0.1)
    else:
        effective_transition = TRANSITION_DURATION

    # Construir entradas de imágenes: una por imagen, usando loop 1.
    cmd: List[str] = [ffmpeg_exe, "-y"]
    for img_path, dur in image_entries:
        norm_path = _normalize_path_for_ffmpeg(img_path)
        dur_safe = max(dur, 0.01)
        cmd += ["-loop", "1", "-t", f"{dur_safe:.6f}", "-i", norm_path]

    # Añadir el audio de narración como entrada adicional.
    voice_index = len(image_entries)
    cmd += ["-i", wav_path]

    # Añadir música (si existe) como entrada adicional.
    music_index: int | None = None
    if music_wav_path is not None:
        music_index = len(image_entries) + 1
        cmd += ["-i", music_wav_path]

    # Construir filter_complex con scale+crop, efecto opcional y cadena de xfade.
    filter_parts: List[str] = []
    scaled_labels: List[str] = []
    for idx in range(len(image_entries)):
        in_label = f"[{idx}:v]"
        base_label = f"[b{idx}]"
        # Primero, escalar y recortar a 1920x1080 (cover).
        scale_crop = (
            f"{in_label}"
            f"scale='if(gt(a,{WIDTH}/{HEIGHT}),-2,{WIDTH})'"
            f":'if(gt(a,{WIDTH}/{HEIGHT}),{HEIGHT},-2)',"
            f"crop={WIDTH}:{HEIGHT}{base_label}"
        )
        filter_parts.append(scale_crop)

        # Después, aplicar efecto por imagen (si está activo).
        if IMAGE_EFFECT == "pulse":
            out_label = f"[v{idx}]"
            period_frames = PULSE_PERIOD * FPS if PULSE_PERIOD > 0 else FPS
            zoom_expr = (
                f"{base_label}"
                f"zoompan=z='1+{PULSE_STRENGTH}*sin(2*PI*on/{period_frames})'"
                ":x='(iw-ow)/2':y='(ih-oh)/2'"
                f":s={WIDTH}x{HEIGHT}:fps={FPS}{out_label}"
            )
            filter_parts.append(zoom_expr)
        else:
            # Sin efecto: usamos directamente la imagen escalada.
            out_label = base_label

        scaled_labels.append(out_label)

    # Si solo hay una imagen, no aplicamos xfade.
    if len(scaled_labels) == 1:
        final_video_label = scaled_labels[0]
    else:
        # Primera transición.
        current_label = scaled_labels[0]
        current_time = durations[0]
        for idx in range(1, len(scaled_labels)):
            next_label = scaled_labels[idx]
            out_label = f"[xf{idx}]"
            transition_type = FIRST_TRANSITION if idx == 1 else OTHER_TRANSITION
            offset = max(current_time - effective_transition, 0.0)
            xfade_part = (
                f"{current_label}{next_label}"
                f"xfade=transition={transition_type}"
                f":duration={effective_transition:.6f}"
                f":offset={offset:.6f}{out_label}"
            )
            filter_parts.append(xfade_part)
            current_time = current_time + durations[idx] - effective_transition
            current_label = out_label
        final_video_label = current_label

    audio_map_label: str
    if music_index is not None:
        # Mezclar narración + música con volumen configurable y aplicar un pequeño fade-in
        # y, opcionalmente, un filtro de denoise.
        voice_label = f"[{voice_index}:a]"
        music_label = f"[{music_index}:a]"
        music_vol_label = "[music_vol]"
        audio_mix_label = "[a_mix]"
        fade_label = "[a_fade]"
        audio_out_label = "[aout]"
        filter_parts.append(
            f"{music_label}volume={music_volume:.3f}{music_vol_label}"
        )
        filter_parts.append(
            f"{voice_label}{music_vol_label}amix=inputs=2:normalize=0{audio_mix_label}"
        )
        # Fade-in muy corto (0.1 s) para suavizar el arranque del audio.
        filter_parts.append(
            f"{audio_mix_label}afade=t=in:st=0:d=0.1{fade_label}"
        )
        if ENABLE_AUDIO_DENOISE:
            filter_parts.append(
                f"{fade_label}{AUDIO_DENOISE_FILTER}{audio_out_label}"
            )
        else:
            # Sin denoise, usar directamente la señal con fade (anull = filtro identidad).
            filter_parts.append(
                f"{fade_label}anull{audio_out_label}"
            )
        audio_map_label = audio_out_label
    else:
        # Solo narración, sin música de fondo: aplicar también un pequeño fade-in
        # y denoise opcional.
        voice_label = f"[{voice_index}:a]"
        fade_label = "[a_fade]"
        audio_out_label = "[aout]"
        filter_parts.append(
            f"{voice_label}afade=t=in:st=0:d=0.1{fade_label}"
        )
        if ENABLE_AUDIO_DENOISE:
            filter_parts.append(
                f"{fade_label}{AUDIO_DENOISE_FILTER}{audio_out_label}"
            )
        else:
            filter_parts.append(
                f"{fade_label}anull{audio_out_label}"
            )
        audio_map_label = audio_out_label

    filter_complex = "; ".join(filter_parts)

    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        final_video_label,
        "-map",
        audio_map_label,
        "-r",
        str(FPS),
    ]

    if use_gpu:
        cmd += [
            "-c:v",
            "h264_nvenc",
            "-gpu",
            str(gpu_index),
            "-pix_fmt",
            "yuv420p",
        ]
    else:
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-pix_fmt",
            "yuv420p",
        ]

    cmd += [
        "-c:a",
        "aac",
        "-shortest",
        output_path,
    ]

    print("Ejecutando FFmpeg para slideshow simple:")
    print("  " + " ".join(cmd))
    t_start = time.perf_counter()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    elapsed = time.perf_counter() - t_start
    if result.returncode != 0:
        print("ERROR: FFmpeg devolvió un código de error al generar el slideshow simple.", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)

    print(f"Listo (slideshow simple FFmpeg): {output_path}")
    print(f"Tiempo de render (FFmpeg): {elapsed:.1f} s ({elapsed / 60:.1f} min)")

    # Limpiar WAV intermedios si no se desean conservar.
    if not KEEP_INTERMEDIATE_AUDIO_FILES:
        for tmp_path in (wav_path, music_wav_path):
            if tmp_path and os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


def collect_parts_audio_and_images(
    audio_dir: str, img_dir: str
) -> List[Tuple[str, List[str]]]:
    tasks: List[Tuple[str, str, List[str]]] = []  # (rel_key, audio_path, image_paths)

    for root, _dirs, files in os.walk(audio_dir):
        for fname in sorted(files):
            if not fname.lower().endswith(".wav"):
                continue
            if not fname.startswith("part"):
                continue
            audio_path = os.path.join(root, fname)
            rel = os.path.relpath(audio_path, audio_dir)
            rel_base = os.path.dirname(rel)
            base_name = os.path.splitext(os.path.basename(fname))[0]  # part001

            img_chapter_dir = os.path.join(img_dir, rel_base) if rel_base else img_dir
            if not os.path.isdir(img_chapter_dir):
                tasks.append((rel, audio_path, []))
                continue

            pattern = re.compile(r"^" + re.escape(base_name) + r"_img(\d+)\.png$", re.IGNORECASE)
            pairs: List[Tuple[int, str]] = []
            for img_name in os.listdir(img_chapter_dir):
                m = pattern.match(img_name)
                if m:
                    num = int(m.group(1))
                    pairs.append((num, os.path.join(img_chapter_dir, img_name)))
            pairs.sort(key=lambda x: x[0])
            image_paths = [p[1] for p in pairs]
            tasks.append((rel, audio_path, image_paths))

    tasks.sort(key=lambda t: t[0])
    return [(audio_path, image_paths) for _rel, audio_path, image_paths in tasks]


def _scale_to_fit(w_orig: int, h_orig: int, w_max: int, h_max: int) -> Tuple[int, int]:
    if w_orig <= 0 or h_orig <= 0:
        return w_max, h_max
    scale = min(w_max / w_orig, h_max / h_orig)
    return int(round(w_orig * scale)), int(round(h_orig * scale))


def _scale_to_fill(w_orig: int, h_orig: int, w_max: int, h_max: int) -> Tuple[int, int]:
    if w_orig <= 0 or h_orig <= 0:
        return w_max, h_max
    scale = max(w_max / w_orig, h_max / h_orig)
    return int(round(w_orig * scale)), int(round(h_orig * scale))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera un video 1920x1080 a partir de audio/ e img/ del libro."
    )
    parser.add_argument(
        "book",
        nargs="?",
        default=BOOK_DIR,
        help="Carpeta del libro (ej. jardin) o ruta a ella.",
    )
    parser.add_argument("--base-dir", help="Directorio base si book es solo el nombre.")
    parser.add_argument(
        "--image-duration",
        type=float,
        default=None,
        metavar="SECS",
        help="Segundos por imagen (por defecto: reparto equitativo según audio).",
    )
    parser.add_argument(
        "--music-volume",
        type=float,
        default=MUSIC_VOLUME,
        metavar="FACTOR",
        help="Volumen relativo de la música de fondo (1.0 = mismo volumen que la narración).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Nombre del archivo de salida (ej. mi_video.mp4).",
    )
    parser.add_argument(
        "--gpu",
        action="store_true",
        default=True,
        help="Intentar codificación por GPU NVIDIA (NVENC). (default: True)",
    )
    parser.add_argument(
        "--no-gpu",
        action="store_false",
        dest="gpu",
        help="Usar solo CPU (libx264), sin intentar GPU.",
    )
    parser.add_argument(
        "--preset",
        choices=("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"),
        default="fast",
        help="Preset de libx264 cuando se usa CPU (default: fast).",
    )
    parser.add_argument(
        "--gpu-index",
        type=int,
        default=0,
        metavar="N",
        help="Índice de la GPU NVIDIA a usar (0 = primera, por defecto). Solo aplica con --gpu.",
    )
    parser.add_argument(
        "--export-resolve",
        action="store_true",
        help="Generar EDL + WAV concatenado + README para DaVinci Resolve (no renderizar con MoviePy).",
    )
    args = parser.parse_args()

    book_name, book_dir, audio_dir, img_dir, video_dir = resolve_book_dirs(
        args.book, args.base_dir
    )

    duration_per_image = args.image_duration if args.image_duration is not None else IMAGE_DURATION_SECONDS

    # Descubrir carpeta de música opcional.
    music_dir = os.path.join(book_dir, "music")
    music_paths: List[str] = []
    if os.path.isdir(music_dir):
        for fname in sorted(os.listdir(music_dir)):
            if fname.lower().endswith(".wav"):
                music_paths.append(os.path.join(music_dir, fname))

    print(f"Libro: {book_name}")
    print(f"Audio: {audio_dir}")
    print(f"Imágenes: {img_dir}")
    if music_paths:
        print(f"Música de fondo: {music_dir} ({len(music_paths)} archivo(s) WAV)")
    else:
        print("Música de fondo: (ninguna pista WAV encontrada en 'music/')")
    print(f"Salida video: {video_dir}")

    parts = collect_parts_audio_and_images(audio_dir, img_dir)
    if not parts:
        print("No se encontraron partes (audio .wav) para procesar.")
        return

    print(f"Partes encontradas: {len(parts)}")

    if args.export_resolve:
        edl_entries: List[Tuple[str, float]] = []
        for audio_path, image_paths in parts:
            if not image_paths:
                continue
            audio_duration = get_audio_duration_seconds(audio_path)
            if AUDIO_TRIM_START_SECONDS > 0.0:
                audio_duration = max(0.0, audio_duration - AUDIO_TRIM_START_SECONDS)
            n = len(image_paths)
            dur_per = (
                duration_per_image
                if duration_per_image is not None
                else (audio_duration / n if n else 0.0)
            )
            for img_path in image_paths:
                edl_entries.append((img_path, dur_per))
        audio_paths_ordered = [p[0] for p in parts]
        edl_path = os.path.join(video_dir, f"{book_name}.edl")
        wav_path = os.path.join(video_dir, f"{book_name}_audio.wav")
        readme_path = os.path.join(video_dir, "README_Resolve.txt")
        if edl_entries:
            write_edl(
                edl_entries,
                edl_path,
                fps=FPS,
                transition_duration_sec=TRANSITION_DURATION,
            )
            print(f"EDL: {edl_path}")
        concat_audio_to_wav(
            audio_paths_ordered,
            wav_path,
            trim_start_seconds=AUDIO_TRIM_START_SECONDS,
        )
        print(f"Audio: {wav_path}")
        readme_text = (
            "Importar proyecto en DaVinci Resolve\n"
            "====================================\n\n"
            "1. En Resolve: importar al Media Pool la carpeta 'img/' (y subcarpetas) y el archivo\n"
            f"   '{book_name}_audio.wav'.\n\n"
            "2. File > Import Timeline > EDL (o AAF/EDL/XML) y seleccionar el archivo\n"
            f"   '{book_name}.edl'.\n\n"
            "3. Asignar el audio '{book_name}_audio.wav' a la pista de audio de la timeline\n"
            "   si no queda vinculado.\n\n"
            "4. Ajustar la secuencia a 1920x1080, 24 fps si hace falta.\n\n"
            "5. Renderizar."
        )
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(readme_text)
        print(f"README: {readme_path}")
        return

    ffmpeg_env = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if sys.platform == "win32":
        _default_ffmpeg = r"C:\Users\Yonex\Downloads\ffmpeg\bin\ffmpeg.exe"
        if (not ffmpeg_env or not os.path.isfile(ffmpeg_env)) and os.path.isfile(_default_ffmpeg):
            os.environ["IMAGEIO_FFMPEG_EXE"] = _default_ffmpeg
            ffmpeg_env = _default_ffmpeg
    try:
        import imageio_ffmpeg
        _ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        _ffmpeg_exe = ffmpeg_env
    if not _ffmpeg_exe or not os.path.isfile(_ffmpeg_exe):
        print("ERROR: No se encontró el ejecutable de FFmpeg.", file=sys.stderr)
        if os.environ.get("IMAGEIO_FFMPEG_EXE"):
            print(f"   IMAGEIO_FFMPEG_EXE = {os.environ.get('IMAGEIO_FFMPEG_EXE')!r}", file=sys.stderr)
            print("   Debe ser la ruta completa al archivo ffmpeg.exe (no a la carpeta).", file=sys.stderr)
            print("   Ejemplo: C:\\Users\\Yonex\\Downloads\\ffmpeg\\bin\\ffmpeg.exe", file=sys.stderr)
        else:
            print("   Si usas un FFmpeg propio, define IMAGEIO_FFMPEG_EXE con la ruta al ejecutable:", file=sys.stderr)
            print('   PowerShell: $env:IMAGEIO_FFMPEG_EXE = "C:\\Users\\Yonex\\Downloads\\ffmpeg\\bin\\ffmpeg.exe"', file=sys.stderr)
        sys.exit(1)

    # Modo por defecto: slideshow simple con FFmpeg, sin MoviePy.
    # Para evitar errores de Windows por comandos demasiado largos (WinError 206),
    # generamos un MP4 por capítulo (subcarpeta de audio/img) en lugar de uno solo gigante.
    use_gpu_simple = args.gpu
    if use_gpu_simple and not _ffmpeg_has_nvenc():
        print("AVISO: El FFmpeg configurado no incluye NVENC (GPU). Se usará CPU (libx264).")
        use_gpu_simple = False
    gpu_index_simple = args.gpu_index

    # Agrupar partes por "capítulo" según la subcarpeta relativa dentro de audio_dir.
    chapters: dict[str, list[tuple[str, list[str]]]] = {}
    for audio_path, image_paths in parts:
        rel = os.path.relpath(audio_path, audio_dir)
        chapter_key = os.path.dirname(rel)  # '' si está en la raíz
        chapters.setdefault(chapter_key, []).append((audio_path, image_paths))

    print("Usando slideshow simple con FFmpeg (sin MoviePy), exportando por capítulos:")
    for chapter_key, chapter_parts in chapters.items():
        # Sufijo seguro para el nombre del archivo de salida.
        # Ej: '' -> 'full', 'cap 1' -> 'cap-1', 'cap 1/sub' -> 'cap-1_sub'
        safe_suffix = chapter_key or "full"
        safe_suffix = safe_suffix.replace(os.sep, "_")
        safe_suffix = re.sub(r"\s+", "-", safe_suffix)
        chapter_book_name = f"{book_name}-{safe_suffix}"
        chapter_out_name = f"{chapter_book_name}.mp4"
        chapter_output_path = os.path.join(video_dir, chapter_out_name)

        if os.path.isfile(chapter_output_path):
            print(
                f"  - Capítulo '{chapter_key or 'root'}': ya existe {chapter_out_name}, se omite."
            )
            continue

        print(
            f"  - Capítulo '{chapter_key or 'root'}' -> {chapter_out_name} "
            f"({len(chapter_parts)} parte(s))"
        )

        _export_simple_slideshow_with_ffmpeg(
            book_name=chapter_book_name,
            video_dir=video_dir,
            parts=chapter_parts,
            duration_per_image=duration_per_image,
            audio_trim_start_seconds=max(AUDIO_TRIM_START_SECONDS, 0.0),
            music_paths=music_paths,
            music_volume=args.music_volume,
            ffmpeg_exe=_ffmpeg_exe,
            use_gpu=use_gpu_simple,
            gpu_index=gpu_index_simple,
            preset=args.preset,
        )

    return


if __name__ == "__main__":
    main()
