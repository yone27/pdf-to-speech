import argparse
import os
import re
import subprocess
import sys
import time
import wave
from typing import List, Tuple

BOOK_DIR = "jardin"
OUTPUT_VIDEO_NAME = None
IMAGE_DURATION_SECONDS: float | None = None

# Efecto Ken Burns simple en modo FFmpeg (no usado actualmente, placeholder para futuras mejoras).
ZOOM_IN_OUT = False

# Pausa de silencio entre audios al concatenar (en segundos). 0.0 = sin pausa.
AUDIO_GAP_SECONDS = 1.0

WIDTH = 1920
HEIGHT = 1080
FPS = 24


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


def concat_audio_to_wav(audio_paths: List[str], output_wav_path: str) -> None:
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

    concat_audio_to_wav(audio_paths_ordered, wav_path)
    print(f"Audio concatenado: {wav_path}")

    with open(images_list_path, "w", encoding="utf-8") as f:
        for idx, (img_path, dur) in enumerate(image_entries):
            norm_path = _normalize_path_for_ffmpeg(img_path)
            f.write(f"file '{norm_path}'\n")
            if idx < len(image_entries) - 1:
                dur_safe = max(dur, 0.01)
                f.write(f"duration {dur_safe:.6f}\n")

    print(f"Lista de imágenes para FFmpeg: {images_list_path}")

    out_name = f"{book_name}.mp4"
    if out_name.lower().endswith(".mp4") is False:
        out_name += ".mp4"
    output_path = os.path.join(video_dir, out_name)

    if ZOOM_IN_OUT and duration_per_image is not None and duration_per_image > 0:
        d = duration_per_image
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

    use_gpu_simple = args.gpu
    if use_gpu_simple and not _ffmpeg_has_nvenc():
        print("AVISO: El FFmpeg configurado no incluye NVENC (GPU). Se usará CPU (libx264).")
        use_gpu_simple = False
    gpu_index_simple = args.gpu_index
    print("Usando slideshow simple con FFmpeg (sin MoviePy).")
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


if __name__ == "__main__":
    main()
