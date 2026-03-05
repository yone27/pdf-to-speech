import os
import re
import struct

# Carpeta del libro (nombre o ruta). El script usará {carpeta}/audio/ y unirá los part*.wav
BOOK_FOLDER = "el-muro"

WAV_HEADER_SIZE = 44
# Posición del sample rate en la cabecera WAV (fmt chunk): bytes 24-27, little-endian
SAMPLE_RATE_OFFSET = 24


def _natural_sort_key(name: str):
    """Ordena part001.wav, part002.wav, ... part010.wav en orden numérico."""
    m = re.search(r"(\d+)", name)
    return (0, 0) if not m else (int(m.group(1)), name)


def get_audio_dir(book_arg: str) -> str:
    """Devuelve la ruta absoluta a la carpeta audio del libro."""
    path = os.path.abspath(book_arg)
    if not os.path.isdir(path):
        raise FileNotFoundError(f"No existe la carpeta: {path}")
    audio_dir = os.path.join(path, "audio")
    if not os.path.isdir(audio_dir):
        raise FileNotFoundError(f"No existe la carpeta de audio: {audio_dir}")
    return audio_dir


def list_wav_parts(audio_dir: str) -> list[str]:
    """Lista los .wav en audio_dir ordenados por número (part001, part002, ...)."""
    files = [f for f in os.listdir(audio_dir) if f.lower().endswith(".wav")]
    files.sort(key=_natural_sort_key)
    return [os.path.join(audio_dir, f) for f in files]


def read_wav_pcm(filepath: str) -> tuple[bytes, int]:
    """Lee un WAV y devuelve (datos_pcm, sample_rate). Asume cabecera estándar de 44 bytes."""
    with open(filepath, "rb") as f:
        header = f.read(WAV_HEADER_SIZE)
        pcm = f.read()
    sample_rate = struct.unpack("<I", header[SAMPLE_RATE_OFFSET : SAMPLE_RATE_OFFSET + 4])[0]
    return pcm, sample_rate


def write_wav(filepath: str, pcm: bytes, sample_rate: int, channels: int = 1) -> None:
    """Escribe un archivo WAV con cabecera a partir de PCM 16-bit."""
    n = len(pcm)
    byte_rate = sample_rate * channels * 2
    block_align = channels * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + n,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        16,
        b"data",
        n,
    )
    with open(filepath, "wb") as f:
        f.write(header)
        f.write(pcm)


def main() -> None:
    audio_dir = get_audio_dir(BOOK_FOLDER)
    parts = list_wav_parts(audio_dir)
    if not parts:
        print(f"No se encontraron archivos .wav en {audio_dir}")
        return

    print(f"Carpeta audio: {audio_dir}")
    print(f"Archivos a unir ({len(parts)}): {[os.path.basename(p) for p in parts]}")

    pcm_chunks: list[bytes] = []
    sample_rate = 24000
    for i, path in enumerate(parts):
        pcm, sr = read_wav_pcm(path)
        if i == 0:
            sample_rate = sr
        pcm_chunks.append(pcm)

    full_pcm = b"".join(pcm_chunks)
    output_path = os.path.join(audio_dir, "completo.wav")
    write_wav(output_path, full_pcm, sample_rate)
    print(f"✅ Audio unido guardado en: {output_path}")


if __name__ == "__main__":
    main()
