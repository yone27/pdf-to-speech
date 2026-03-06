import argparse
import os
import re
import subprocess
import sys
import time
import wave
from typing import List, Tuple

try:
    from proglog import ProgressBarLogger
except ImportError:
    ProgressBarLogger = None  # type: ignore[misc, assignment]

from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    concatenate_audioclips,
    concatenate_videoclips,
)
from moviepy.video.fx import CrossFadeIn, Resize

BOOK_DIR = "jardin"
OUTPUT_VIDEO_NAME = None
IMAGE_DURATION_SECONDS: float | None = None

# Zoom suave (modo MoviePy): factor de escala al final del clip (1.0 = sin zoom; 1.05 = 5% zoom)
# Impar (1ª, 3ª, ...) = zoom in; par (2ª, 4ª, ...) = zoom out
ZOOM_FACTOR = 1.05

# Efecto Ken Burns simple en modo FFmpeg (--simple-ffmpeg):
# - Cuando está a True, se aplica un zoom in/out suave alternando entre imágenes pares/impares.
ZOOM_IN_OUT = True

# Pausa de silencio entre audios al concatenar (en segundos). 0.0 = sin pausa.
AUDIO_GAP_SECONDS = 1

# Transición entre imágenes: "book" (mapeado a dissolve por ahora), "dissolve", "none"
TRANSITION_TYPE = "book"
TRANSITION_DURATION = 0.8

WIDTH = 1920
HEIGHT = 1080
FPS = 24


def _ffmpeg_has_nvenc() -> bool:
    """Comprueba si el FFmpeg que usa MoviePy tiene soporte NVENC (GPU NVIDIA)."""
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
    """Devuelve el nombre de la GPU NVIDIA en el índice dado (ej. 0) o None si no se puede obtener."""
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


class VideoExportLogger(ProgressBarLogger if ProgressBarLogger else object):  # type: ignore[misc]
    """Logger que muestra barra de progreso y ETA durante write_videofile."""

    def __init__(self, total_duration: float, min_time_interval: float = 1.0, **kwargs):
        if ProgressBarLogger is not None:
            super().__init__(min_time_interval=min_time_interval, **kwargs)
        self._t_start = time.perf_counter()
        self._total_duration = total_duration
        self._last_eta_print = 0.0
        self._min_interval = min_time_interval

    def bars_callback(self, bar: str, attr: str, value, old_value=None) -> None:
        if ProgressBarLogger is not None:
            super().bars_callback(bar, attr, value, old_value)
        bars = getattr(self, "bars", None)
        if attr != "index" or bars is None or bar not in bars:
            return
        info = bars[bar]
        total = info.get("total") or 0
        if total <= 0 or value is None:
            return
        p = value / total
        # No mostrar ETA con muy poco progreso: al inicio avanza lento y la ETA sale enorme
        if p <= 0.03:
            return
        now = time.perf_counter()
        elapsed = now - self._t_start
        if elapsed < 15:
            return
        if now - self._last_eta_print < self._min_interval:
            return
        self._last_eta_print = now
        remaining_sec = elapsed * (1 - p) / p if p > 0 else 0
        mins = int(remaining_sec // 60)
        secs = int(remaining_sec % 60)
        eta_str = f"ETA: ~{mins} min {secs} s restantes"
        try:
            sys.stdout.write(f"\r  {eta_str}    ")
            sys.stdout.flush()
        except Exception:
            pass


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
    """Convierte segundos a timecode HH:MM:SS:FF."""
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


def concat_audio_to_wav(audio_paths: List[str], output_wav_path: str) -> None:
    """Concatena varios WAV en orden y escribe un único archivo WAV."""
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
                out_wav.writeframes(inp.readframes(inp.getnframes()))

            # Insertar silencio entre audios si se ha configurado
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
    """
    Escribe un EDL CMX 3600 con un evento por imagen.
    entries: lista de (image_path, duration_sec) en orden de timeline.
    """
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
    """
    Normaliza una ruta para usarla en archivos de lista de FFmpeg.
    - Usa barras '/' para evitar problemas con '\'.
    - No hace ningún escapado especial; se asume que se envuelve en comillas.
    """
    return os.path.abspath(path).replace("\\", "/")


def _export_simple_slideshow_with_ffmpeg(
    book_name: str,
    video_dir: str,
    parts: List[Tuple[str, List[str]]],
    duration_per_image: float | None,
    ffmpeg_exe: str,
    use_gpu: bool,
    gpu_index: int,
    preset: str,
) -> None:
    """
    Modo rápido: genera un slideshow sencillo (cortes directos entre imágenes, sin zoom ni
    transiciones) usando directamente FFmpeg, y concatena todo el audio en un WAV.
    """
    # Construir lista global (imagen, duración) y lista de audios en orden
    image_entries: List[Tuple[str, float]] = []
    audio_paths_ordered: List[str] = []
    for audio_path, image_paths in parts:
        audio_paths_ordered.append(audio_path)
        if not image_paths:
            continue
        audio_duration = get_audio_duration_seconds(audio_path)
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
    images_list_path = os.path.join(video_dir, f"{book_name}_images.txt")

    # Concatenar audio a un solo WAV
    concat_audio_to_wav(audio_paths_ordered, wav_path)
    print(f"Audio concatenado: {wav_path}")

    # Escribir archivo de lista para -f concat
    with open(images_list_path, "w", encoding="utf-8") as f:
        for idx, (img_path, dur) in enumerate(image_entries):
            norm_path = _normalize_path_for_ffmpeg(img_path)
            # El demuxer concat de FFmpeg espera rutas sin comillas o con comillas simples.
            # Usamos comillas simples para soportar espacios en rutas.
            f.write(f"file '{norm_path}'\n")
            # FFmpeg concat ignora el 'duration' de la última entrada, así que solo lo
            # escribimos para las anteriores. Esto evita que la última quede demasiado corta.
            if idx < len(image_entries) - 1:
                # Forzar un mínimo de duración positiva
                dur_safe = max(dur, 0.01)
                f.write(f"duration {dur_safe:.6f}\n")

    print(f"Lista de imágenes para FFmpeg: {images_list_path}")

    out_name = f"{book_name}.mp4"
    if out_name.lower().endswith(".mp4") is False:
        out_name += ".mp4"
    output_path = os.path.join(video_dir, out_name)

    # Filtro de vídeo para modo simple:
    # - Si ZOOM_IN_OUT está activado y duration_per_image está definido, aplicamos un efecto
    #   Ken Burns sencillo: zoom in/out alterno por imagen usando zoompan.
    # - En caso contrario, usamos un "cover" estático (sin animación) que rellena toda la
    #   pantalla recortando si hace falta, sin deformar la imagen.
    if ZOOM_IN_OUT and duration_per_image is not None and duration_per_image > 0:
        d = duration_per_image
        # seg = floor(t/d), phase = t - seg*d, p = phase/d en [0,1]
        # Imágenes pares (seg % 2 == 0): zoom de 1.0 a 1.05
        # Imágenes impares: zoom de 1.05 a 1.0
        vf_expr = (
            "zoompan="
            "z='if(eq(mod(floor(t/"
            f"{d}"
            "),2),0,"
            "1+0.05*((t-floor(t/"
            f"{d}"
            ")*"
            f"{d}"
            ")/"
            f"{d}"
            "),"
            "1.05-0.05*((t-floor(t/"
            f"{d}"
            ")*"
            f"{d}"
            ")/"
            f"{d}"
            "))')"
            ":x='(iw-ow)/2':y='(ih-oh)/2'"
            f":s={WIDTH}x{HEIGHT}:fps={FPS}"
        )
    else:
        # Escalar manteniendo proporción y rellenar toda la pantalla, recortando si hace falta:
        # - Si la imagen es más panorámica que el vídeo, se escala por alto (HEIGHT) y se recorta a WIDTH.
        # - Si es más vertical, se escala por ancho (WIDTH) y se recorta a HEIGHT.
        # Esto emula un "cover" estático sin deformar la imagen.
        vf_expr = (
            f"scale='if(gt(a,{WIDTH}/{HEIGHT}),-2,{WIDTH})'"
            f":'if(gt(a,{WIDTH}/{HEIGHT}),{HEIGHT},-2)',"
            f"crop={WIDTH}:{HEIGHT}"
        )
    cmd: List[str] = [
        ffmpeg_exe,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        images_list_path,
        "-i",
        wav_path,
        "-vf",
        vf_expr,
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

    # Audio: AAC, duración limitada al más corto (slideshow o audio)
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
    """Escala para cubrir todo el frame (sin barras); el exceso se recorta al centrar."""
    if w_orig <= 0 or h_orig <= 0:
        return w_max, h_max
    scale = max(w_max / w_orig, h_max / h_orig)
    return int(round(w_orig * scale)), int(round(h_orig * scale))


def make_image_clip(
    image_path: str,
    duration: float,
    image_index: int,
    zoom_factor: float = 1.05,
) -> CompositeVideoClip:
    img = ImageClip(image_path)
    w_orig, h_orig = img.w, img.h
    w_fill, h_fill = _scale_to_fill(w_orig, h_orig, WIDTH, HEIGHT)

    img = img.with_duration(duration).with_effects([Resize((w_fill, h_fill))])
    bg = ColorClip((WIDTH, HEIGHT), color=(0, 0, 0)).with_duration(duration)
    img = img.with_position("center")

    if zoom_factor != 1.0:
        zoom_in = image_index % 2 == 0  # 0-based: 0,2,4 -> zoom in
        if zoom_in:
            img = img.with_effects([Resize(lambda t: 1.0 + (zoom_factor - 1.0) * min(1.0, t / max(0.01, duration)))])
        else:
            img = img.with_effects([Resize(lambda t: zoom_factor - (zoom_factor - 1.0) * min(1.0, t / max(0.01, duration)))])

    composite = CompositeVideoClip([bg, img]).with_duration(duration)
    return composite


def apply_transition(
    clip_a: CompositeVideoClip,
    clip_b: CompositeVideoClip,
    transition_duration: float,
    transition_type: str,
) -> CompositeVideoClip:
    if transition_type == "none" or transition_duration <= 0:
        return concatenate_videoclips([clip_a, clip_b], method="chain")

    clip_b_fade = clip_b.with_effects([CrossFadeIn(transition_duration)])
    clip_b_fade = clip_b_fade.with_start(clip_a.duration - transition_duration)
    composite = CompositeVideoClip([clip_a, clip_b_fade])
    composite = composite.with_duration(clip_a.duration + clip_b.duration - transition_duration)
    return composite


def build_video_for_part(
    audio_path: str,
    image_paths: List[str],
    duration_per_image: float | None,
    zoom_factor: float,
    transition_duration: float,
    transition_type: str,
) -> Tuple[CompositeVideoClip | None, AudioFileClip | None]:
    if not image_paths:
        audio_clip = AudioFileClip(audio_path)
        bg = ColorClip((WIDTH, HEIGHT), color=(0, 0, 0)).with_duration(audio_clip.duration)
        return bg, audio_clip

    audio_duration = get_audio_duration_seconds(audio_path)
    n = len(image_paths)
    if duration_per_image is not None:
        dur_per = duration_per_image
    else:
        dur_per = audio_duration / n if n else 0.0

    clips: List[CompositeVideoClip] = []
    for i, img_path in enumerate(image_paths):
        clip = make_image_clip(img_path, dur_per, i, zoom_factor=zoom_factor)
        clips.append(clip)

    if not clips:
        audio_clip = AudioFileClip(audio_path)
        bg = ColorClip((WIDTH, HEIGHT), color=(0, 0, 0)).with_duration(audio_clip.duration)
        return bg, audio_clip

    current = clips[0]
    for next_clip in clips[1:]:
        current = apply_transition(current, next_clip, transition_duration, transition_type)
    video_part = current
    audio_clip = AudioFileClip(audio_path)
    if duration_per_image is not None and audio_clip.duration > video_part.duration:
        audio_clip = audio_clip.subclip(0, video_part.duration)
    return video_part, audio_clip


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
        "--zoom",
        type=float,
        default=ZOOM_FACTOR,
        help=f"Factor de zoom suave (default {ZOOM_FACTOR}).",
    )
    parser.add_argument(
        "--transition",
        choices=("book", "dissolve", "none"),
        default=TRANSITION_TYPE,
        help="Transición entre imágenes. 'book' = dissolve por ahora.",
    )
    parser.add_argument(
        "--transition-duration",
        type=float,
        default=TRANSITION_DURATION,
        help="Duración de la transición en segundos.",
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
    parser.add_argument(
        "--simple-ffmpeg",
        action="store_true",
        help="Modo rápido: generar slideshow simple (sin zoom ni transiciones) usando FFmpeg directo, sin MoviePy.",
    )
    args = parser.parse_args()

    trans_type = args.transition
    if trans_type == "book":
        trans_type = "dissolve"

    book_name, book_dir, audio_dir, img_dir, video_dir = resolve_book_dirs(
        args.book, args.base_dir
    )

    duration_per_image = args.image_duration if args.image_duration is not None else IMAGE_DURATION_SECONDS

    print(f"Libro: {book_name}")
    print(f"Audio: {audio_dir}")
    print(f"Imágenes: {img_dir}")
    print(f"Salida video: {video_dir}")

    parts = collect_parts_audio_and_images(audio_dir, img_dir)
    if not parts:
        print("No se encontraron partes (audio .wav) para procesar.")
        return

    print(f"Partes encontradas: {len(parts)}")

    if args.export_resolve:
        # Construir lista (image_path, duration_sec) en orden de timeline
        edl_entries: List[Tuple[str, float]] = []
        for audio_path, image_paths in parts:
            if not image_paths:
                continue
            audio_duration = get_audio_duration_seconds(audio_path)
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
                transition_duration_sec=args.transition_duration,
            )
            print(f"EDL: {edl_path}")
        concat_audio_to_wav(audio_paths_ordered, wav_path)
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

    # Configurar y comprobar que el FFmpeg que usará MoviePy / FFmpeg directo existe
    ffmpeg_env = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if sys.platform == "win32":
        _default_ffmpeg = r"C:\Users\Yonex\Downloads\ffmpeg\bin\ffmpeg.exe"
        # Si la variable no está bien apuntando a un archivo, usar el default del usuario si existe
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

    # Modo rápido: slideshow simple con FFmpeg, sin MoviePy
    if args.simple_ffmpeg:
        use_gpu_simple = args.gpu
        if use_gpu_simple and not _ffmpeg_has_nvenc():
            print("AVISO: El FFmpeg configurado no incluye NVENC (GPU). Se usará CPU (libx264).")
            use_gpu_simple = False
        gpu_index_simple = args.gpu_index
        print("Usando modo rápido: slideshow simple con FFmpeg (sin MoviePy).")
        _export_simple_slideshow_with_ffmpeg(
            book_name=book_name,
            video_dir=video_dir,
            parts=parts,
            duration_per_image=duration_per_image,
            ffmpeg_exe=_ffmpeg_exe,
            use_gpu=use_gpu_simple,
            gpu_index=gpu_index_simple,
            preset=args.preset,
        )
        return

    t_render_start = time.perf_counter()

    video_clips: List[CompositeVideoClip] = []
    audio_clips: List[AudioFileClip] = []

    for idx, (audio_path, image_paths) in enumerate(parts, start=1):
        print(f"[{idx}/{len(parts)}] {os.path.basename(audio_path)} ({len(image_paths)} imágenes)")
        video_part, audio_part = build_video_for_part(
            audio_path,
            image_paths,
            duration_per_image=duration_per_image,
            zoom_factor=args.zoom,
            transition_duration=args.transition_duration,
            transition_type=trans_type,
        )
        if video_part is not None:
            video_clips.append(video_part)
        if audio_part is not None:
            audio_clips.append(audio_part)

    if not video_clips or not audio_clips:
        print("No hay clips para concatenar.")
        return

    print("[1/4] Concatenando videoclips...")
    final_video = concatenate_videoclips(video_clips, method="chain")
    print("[2/4] Concatenando audioclips...")
    final_audio = concatenate_audioclips(audio_clips)
    print("[3/4] Asignando audio al video...")
    final_video = final_video.with_audio(final_audio)

    out_name = args.output or OUTPUT_VIDEO_NAME or f"{book_name}.mp4"
    if not out_name.lower().endswith(".mp4"):
        out_name += ".mp4"
    output_path = os.path.join(video_dir, out_name)

    use_gpu = args.gpu
    if use_gpu and not _ffmpeg_has_nvenc():
        print("[4/4] AVISO: El FFmpeg que usa MoviePy no incluye NVENC (GPU).")
        print("       Se usará CPU. Para activar GPU: instala FFmpeg con soporte NVIDIA")
        print("       y define la variable de entorno IMAGEIO_FFMPEG_EXE con su ruta.")
        use_gpu = False
    try:
        import imageio_ffmpeg
        _ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        _ffmpeg_exe = "(no detectado)"
    print(f"      FFmpeg usado: {_ffmpeg_exe}")
    gpu_index = args.gpu_index
    if use_gpu:
        gpu_name = _get_nvidia_gpu_name(gpu_index)
        gpu_label = gpu_name if gpu_name else f"NVIDIA GPU {gpu_index}"
        print(f"[4/4] Codificando video con GPU: {gpu_label} (h264_nvenc, dispositivo {gpu_index})...")
        print("      Si 'Video Encode' sigue en 0%, instala un FFmpeg con NVENC y define IMAGEIO_FFMPEG_EXE.")
    else:
        print(f"[4/4] Codificando video con CPU (libx264, preset={args.preset})...")
    print("      (El uso alto de RAM es normal: MoviePy genera los fotogramas en memoria.)")
    export_logger = (
        VideoExportLogger(final_video.duration)
        if ProgressBarLogger is not None
        else "bar"
    )
    write_kw: dict = {
        "fps": FPS,
        "audio_codec": "aac",
        "threads": 4,
        "logger": export_logger,
    }
    if use_gpu:
        write_kw["codec"] = "h264_nvenc"
        # Forzar uso de la GPU en el índice indicado (evita usar la integrada en sistemas con varias GPUs)
        # -pix_fmt yuv420p para compatibilidad con reproductores y evitar vídeo negro
        write_kw["ffmpeg_params"] = ["-gpu", str(gpu_index), "-pix_fmt", "yuv420p"]
    else:
        write_kw["codec"] = "libx264"
        write_kw["preset"] = args.preset

    try:
        final_video.write_videofile(output_path, **write_kw)
    except Exception as e:
        if use_gpu:
            print(f"\nGPU (NVENC) no disponible o error: {e}")
            print("Usando CPU (libx264) con preset 'fast'...")
            write_kw["codec"] = "libx264"
            write_kw["preset"] = args.preset
            write_kw.pop("ffmpeg_params", None)
            final_video.write_videofile(output_path, **write_kw)
        else:
            raise
    print()  # nueva línea tras la barra/ETA

    final_video.close()
    final_audio.close()
    for c in video_clips:
        c.close()
    for c in audio_clips:
        c.close()

    elapsed = time.perf_counter() - t_render_start
    print(f"Listo: {output_path}")
    print(f"Tiempo de renderizado: {elapsed:.1f} s ({elapsed / 60:.1f} min)")


if __name__ == "__main__":
    main()
