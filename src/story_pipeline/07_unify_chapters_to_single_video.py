import argparse
import os
import subprocess
import sys
import time
from typing import List, Tuple


BOOK_DIR_DEFAULT = "guion-en"
GAP_SECONDS_DEFAULT = 1.0
TIME_FORMAT_DEFAULT = "mmss" 


def _resolve_book_paths(book_arg: str, base_dir: str | None = None) -> Tuple[str, str, str, str]:
    path = os.path.abspath(book_arg)
    if os.path.isdir(path):
        book_dir = path
        book_name = os.path.basename(book_dir)
    else:
        base = os.path.abspath(base_dir or os.getcwd())
        book_name = book_arg
        book_dir = os.path.join(base, book_name)

    video_dir = os.path.join(book_dir, "video")
    guion_txt_path = os.path.join(book_dir, "guion.txt")

    if not os.path.isdir(video_dir):
        raise FileNotFoundError(f"No existe la carpeta de video: {video_dir}")

    if not os.path.isfile(guion_txt_path):
        # En tu estructura actual, guion.txt está un nivel por encima de la carpeta del libro.
        alt_guion = os.path.join(os.path.dirname(book_dir), "guion.txt")
        if os.path.isfile(alt_guion):
            guion_txt_path = alt_guion
        else:
            raise FileNotFoundError(f"No se encontró guion.txt en: {guion_txt_path} ni en: {alt_guion}")

    return book_name, book_dir, video_dir, guion_txt_path


def _discover_videos(video_dir: str) -> List[str]:
    videos = [
        os.path.join(video_dir, fname)
        for fname in sorted(os.listdir(video_dir))
        if fname.lower().endswith(".mp4")
    ]
    if not videos:
        raise FileNotFoundError(f"No se encontraron archivos .mp4 en {video_dir}")
    return videos


def _get_ffmpeg_exe() -> str:
    env_ffmpeg = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if env_ffmpeg and os.path.isfile(env_ffmpeg):
        return env_ffmpeg

    if sys.platform == "win32":
        default_ffmpeg = r"C:\Users\Yonex\Downloads\ffmpeg\bin\ffmpeg.exe"
        if os.path.isfile(default_ffmpeg):
            return default_ffmpeg

    return "ffmpeg"


def _get_ffprobe_exe(ffmpeg_exe: str) -> str:
    if ffmpeg_exe and os.path.isabs(ffmpeg_exe):
        ffmpeg_dir = os.path.dirname(ffmpeg_exe)
        candidate = os.path.join(ffmpeg_dir, "ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if os.path.isfile(candidate):
            return candidate
    return "ffprobe"


def _get_video_duration_seconds(video_path: str, ffprobe_exe: str) -> float:
    cmd = [
        ffprobe_exe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe devolvió un código de error al obtener duración de {video_path}:\n{result.stderr}"
        )
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(
            f"No se pudo parsear la duración de ffprobe para {video_path}: {result.stdout!r}"
        )


def _seconds_to_timestamp(sec: float, mode: str) -> str:
    if sec < 0:
        sec = 0.0
    total = int(round(sec))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if mode == "mmss" and h == 0:
        return f"{m:02d}:{s:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def _extract_chapter_titles(guion_txt_path: str) -> List[str]:
    titles: List[str] = []
    with open(guion_txt_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("# "):
                titles.append(stripped[2:].strip() or "Capítulo")
    return titles


def _build_chapter_timestamps(
    durations: List[float],
    gap_seconds: float,
) -> List[float]:
    starts: List[float] = []
    current = 0.0
    for dur in durations:
        starts.append(current)
        current += max(dur, 0.0) + max(gap_seconds, 0.0)
    return starts


def _write_meta_index(
    meta_path: str,
    video_name: str,
    timestamps: List[float],
    durations: List[float],
    titles: List[str],
    time_format: str,
) -> None:
    if not timestamps:
        return

    lines: List[str] = []
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    lines.append("")
    lines.append("")
    lines.append("===================================")
    lines.append("")
    lines.append(f"# Índice YouTube - {video_name} ({now_str})")

    for idx, start_sec in enumerate(timestamps):
        # Calculamos el final real del capítulo usando la duración del vídeo correspondiente
        if idx < len(durations):
            end_sec = start_sec + max(durations[idx], 0.0)
        else:
            end_sec = start_sec

        ts_start = _seconds_to_timestamp(start_sec, time_format)
        ts_end = _seconds_to_timestamp(end_sec, time_format)
        title = titles[idx] if idx < len(titles) else f"Capítulo {idx + 1}"
        # Formato: "0:00 - 1:03 Título del capítulo"
        lines.append(f"{ts_start} - {ts_end} {title}")

    with open(meta_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")


def _concat_videos_with_ffmpeg(
    video_paths: List[str],
    output_path: str,
    ffmpeg_exe: str,
) -> None:
    if not video_paths:
        raise ValueError("No hay vídeos para concatenar.")

    tmp_list_path = os.path.join(os.path.dirname(output_path), "concat_list.txt")
    with open(tmp_list_path, "w", encoding="utf-8") as f:
        for path in video_paths:
            norm = os.path.abspath(path).replace("\\", "/")
            f.write(f"file '{norm}'\n")

    cmd = [
        ffmpeg_exe,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        tmp_list_path,
        "-c",
        "copy",
        output_path,
    ]

    print("Concatenando vídeos con FFmpeg:")
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
    try:
        os.remove(tmp_list_path)
    except OSError:
        pass

    if result.returncode != 0:
        print("ERROR: FFmpeg devolvió un código de error al concatenar los vídeos.", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)

    print(f"Vídeo unificado generado: {output_path}")
    print(f"Tiempo de concatenación: {elapsed:.1f} s ({elapsed / 60:.1f} min)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unifica los vídeos de video/ en un solo MP4 y genera un índice de capítulos para YouTube en 'meta'."
    )
    parser.add_argument(
        "book",
        nargs="?",
        default=BOOK_DIR_DEFAULT,
        help="Carpeta del libro (ej. guion) o ruta a ella.",
    )
    parser.add_argument("--base-dir", help="Directorio base si book es solo el nombre.")
    parser.add_argument(
        "--gap-seconds",
        type=float,
        default=GAP_SECONDS_DEFAULT,
        metavar="SECS",
        help="Separación lógica entre capítulos usada para el cálculo de timestamps (default: 1.0).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Nombre del archivo de salida (ej. guion-full.mp4). Por defecto: <book_name>-full.mp4 en video/.",
    )
    parser.add_argument(
        "--time-format",
        choices=("mmss", "hhmmss"),
        default=TIME_FORMAT_DEFAULT,
        help="Formato de tiempo para el índice (mmss = MM:SS, hhmmss = HH:MM:SS).",
    )
    args = parser.parse_args()

    book_name, book_dir, video_dir, guion_txt_path = _resolve_book_paths(
        args.book,
        args.base_dir,
    )

    print(f"Libro: {book_name}")
    print(f"Video: {video_dir}")
    print(f"Guion: {guion_txt_path}")

    videos = _discover_videos(video_dir)
    print(f"Vídeos encontrados: {len(videos)}")

    ffmpeg_exe = _get_ffmpeg_exe()
    ffprobe_exe = _get_ffprobe_exe(ffmpeg_exe)

    # 1) Duraciones de cada vídeo.
    durations: List[float] = []
    for path in videos:
        dur = _get_video_duration_seconds(path, ffprobe_exe)
        durations.append(dur)
        print(f"  - {os.path.basename(path)}: {dur:.2f} s")

    # 2) Timestamps de inicio por capítulo (con gap lógico).
    starts = _build_chapter_timestamps(durations, args.gap_seconds)

    # 3) Nombres de capítulos desde guion.txt.
    titles = _extract_chapter_titles(guion_txt_path)
    if titles:
        print(f"Títulos de capítulo detectados en guion.txt: {len(titles)}")
    else:
        print("ADVERTENCIA: No se detectaron líneas de capítulo en guion.txt (líneas que empiecen por '# ').")

    # 4) Construir índice y escribir en meta.
    meta_path = os.path.join(book_dir, "meta")
    output_name = args.output or f"{book_name}-full.mp4"
    _write_meta_index(
        meta_path=meta_path,
        video_name=output_name,
        timestamps=starts,
        durations=durations,
        titles=titles,
        time_format=args.time_format,
    )
    print(f"Índice de capítulos añadido a: {meta_path}")

    # 5) Concatenar vídeos con FFmpeg.
    output_path = os.path.join(video_dir, output_name)
    _concat_videos_with_ffmpeg(
        video_paths=videos,
        output_path=output_path,
        ffmpeg_exe=ffmpeg_exe,
    )


if __name__ == "__main__":
    main()

