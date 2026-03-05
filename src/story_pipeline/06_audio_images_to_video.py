import argparse
import os
import re
import wave
from typing import List, Tuple

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

# Zoom suave: factor de escala al final del clip (1.0 = sin zoom; 1.05 = 5% zoom)
# Impar (1ª, 3ª, ...) = zoom in; par (2ª, 4ª, ...) = zoom out
ZOOM_FACTOR = 1.05

# Transición entre imágenes: "book" (mapeado a dissolve por ahora), "dissolve", "none"
TRANSITION_TYPE = "book"
TRANSITION_DURATION = 0.8

WIDTH = 1920
HEIGHT = 1080
FPS = 24

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


def make_image_clip(
    image_path: str,
    duration: float,
    image_index: int,
    zoom_factor: float = 1.05,
) -> CompositeVideoClip:
    img = ImageClip(image_path)
    w_orig, h_orig = img.w, img.h
    w_fit, h_fit = _scale_to_fit(w_orig, h_orig, WIDTH, HEIGHT)

    img = img.with_duration(duration).with_effects([Resize((w_fit, h_fit))])
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

    print("Concatenando video y audio...")
    final_video = concatenate_videoclips(video_clips, method="chain")
    final_audio = concatenate_audioclips(audio_clips)
    final_video = final_video.with_audio(final_audio)

    out_name = args.output or OUTPUT_VIDEO_NAME or f"{book_name}.mp4"
    if not out_name.lower().endswith(".mp4"):
        out_name += ".mp4"
    output_path = os.path.join(video_dir, out_name)

    print(f"Escribiendo {output_path} ...")
    final_video.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        threads=4,
        logger=None,
    )

    final_video.close()
    final_audio.close()
    for c in video_clips:
        c.close()
    for c in audio_clips:
        c.close()

    print(f"Listo: {output_path}")


if __name__ == "__main__":
    main()
