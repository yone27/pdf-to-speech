import os
import re

# Configuración: edita aquí y ejecuta el script sin argumentos.
SOURCE_TXT = "sumerios.txt"
MAX_CHARS = 750
OUTPUT_DIR = None  # None = se usa la carpeta donde está el TXT; si quieres otra, pon la ruta aquí


def clean_text(text: str) -> str:
    text = re.sub(r"\n+", "\n", text)
    text = re.sub(r" +", " ", text)
    return text.strip()


def _split_long_fragment(fragment: str, max_chars: int) -> list[str]:
    """Si un fragmento supera max_chars, corta preferentemente al final de oración (.!?); si no, al último espacio."""
    result: list[str] = []
    while len(fragment) > max_chars:
        window = fragment[: max_chars + 1]
        # Preferir corte al final de oración
        last_sent = max(
            (i for i, c in enumerate(window) if c in ".!?"),
            default=-1,
        )
        if last_sent >= 0:
            pos = last_sent + 1
            result.append(fragment[:pos].strip())
            fragment = fragment[pos:].lstrip()
        else:
            last_space = window.rfind(" ")
            if last_space > 0:
                result.append(fragment[:last_space].strip())
                fragment = fragment[last_space:].lstrip()
            else:
                result.append(fragment[:max_chars])
                fragment = fragment[max_chars:].lstrip()
    if fragment:
        result.append(fragment.strip())
    return result


def chunk_text(text: str, max_chars: int) -> list[str]:
    # Dividir por . ! ? seguidos de espacios o saltos de línea (no solo espacio)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current_chunk = ""

    for sentence in sentences:
        # Si una "oración" supera el límite, partirla en trozos
        if len(sentence) > max_chars:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            for sub in _split_long_fragment(sentence, max_chars):
                chunks.append(sub)
            continue
        if len(current_chunk) + len(sentence) + 1 <= max_chars:
            current_chunk += sentence + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence + " "

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def get_book_dirs(txt_path: str, base_output: str | None = None) -> tuple[str, str, str]:
    txt_path = os.path.abspath(txt_path)
    book_name = os.path.splitext(os.path.basename(txt_path))[0]

    if base_output:
        base_dir = os.path.abspath(base_output)
    else:
        base_dir = os.path.dirname(txt_path)

    book_dir = os.path.join(base_dir, book_name)
    text_dir = os.path.join(book_dir, "text")
    return book_name, book_dir, text_dir


def main() -> None:
    txt_path = os.path.abspath(SOURCE_TXT)
    if not os.path.isfile(txt_path):
        raise FileNotFoundError(f"No se encontró el TXT: {txt_path}")

    book_name, book_dir, text_dir = get_book_dirs(txt_path, OUTPUT_DIR)
    os.makedirs(text_dir, exist_ok=True)

    print(f"Libro: {book_name}")
    print(f"Carpeta de texto: {text_dir}")

    print("Leyendo TXT...")
    with open(txt_path, "r", encoding="utf-8") as f:
        text = f.read()

    print("Limpiando texto...")
    text = clean_text(text)

    print("Dividiendo en partes...")
    chunks = chunk_text(text, MAX_CHARS)
    print(f"Total de partes: {len(chunks)}")

    for i, chunk in enumerate(chunks, start=1):
        file_name = f"part{str(i).zfill(3)}.txt"
        file_path = os.path.join(text_dir, file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(chunk)

    print(f"✅ Partes guardadas en: {text_dir}")
    print(f"   Para generar audio: python text_to_audiobook.py \"{book_dir}\"")


if __name__ == "__main__":
    main()
