import os
import re

# Configuración: edita aquí y ejecuta el script sin argumentos.
SOURCE_TXT = "odiseaES.txt"
MAX_CHARS = 3500
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


def _section_slug(title: str) -> str:
    """
    Convierte el título de una sección (# Nombre) en un slug para el archivo:
    minúsculas, sin tildes, espacios y caracteres raros reemplazados por nada o guión.
    """
    s = title.strip().lower()
    # Normalizar tildes
    for old, new in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")]:
        s = s.replace(old, new)
    # Solo alfanuméricos y espacios; espacios → nada (o un guión si prefieres)
    s = re.sub(r"[^a-z0-9\s]+", "", s)
    s = re.sub(r"\s+", "", s)  # quitar espacios
    return s or "section"


def split_by_chapters(raw_text: str) -> list[tuple[str, str]]:
    """
    Divide el texto por líneas que empiezan con "# nombre de la sección".
    Devuelve una lista de (section_slug, section_body). Cada sección se troceará
    después para respetar MAX_CHARS.
    """
    lines = raw_text.splitlines()
    sections: list[tuple[str, str]] = []

    # Regex: línea que empieza con # y luego el nombre de la sección
    header_re = re.compile(r"^#\s+(.+)$")

    current_lines: list[str] = []
    current_slug: str | None = None

    for line in lines:
        m = header_re.match(line.strip())
        if m:
            # Guardar bloque anterior (con su slug; si no había header previo = intro)
            if current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    slug = current_slug if current_slug else "intro"
                    sections.append((slug, body))
            # Nuevo bloque: el título es el nombre de esta sección
            current_slug = _section_slug(m.group(1))
            current_lines = []
            continue
        current_lines.append(line)

    if current_lines:
        body = "\n".join(current_lines).strip()
        if body:
            slug = current_slug if current_slug else "section"
            sections.append((slug, body))

    if not sections:
        return [("cap1", raw_text.strip())]

    return sections


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
        raw_text = f.read()

    print("Detectando capítulos...")
    chapters = split_by_chapters(raw_text)
    print(f"Capítulos detectados: {len(chapters)}")

    global_part_index = 1

    for chapter_slug, chapter_body in chapters:
        if not chapter_body.strip():
            continue

        print(f"  Procesando capítulo '{chapter_slug}'...")

        # Limpiar texto de este capítulo y dividir en chunks
        cleaned = clean_text(chapter_body)

        print("  Dividiendo en partes...")
        chapter_chunks = chunk_text(cleaned, MAX_CHARS)
        print(f"    Partes en este capítulo: {len(chapter_chunks)}")

        for part_idx, chunk in enumerate(chapter_chunks, start=1):
            global_name = f"part{str(global_part_index).zfill(3)}"

            if len(chapter_chunks) == 1:
                suffix = f"-{chapter_slug}"
            elif chapter_slug == "prologo":
                suffix = f"-{chapter_slug}part{part_idx}"
            else:
                suffix = f"-{chapter_slug}-part{part_idx}"

            file_name = f"{global_name}{suffix}.txt"
            file_path = os.path.join(text_dir, file_name)

            with open(file_path, "w", encoding="utf-8") as f_out:
                f_out.write(chunk)

            global_part_index += 1

    print(f"✅ Partes guardadas en: {text_dir}")
    print(f"   Para generar audio: python text_to_audiobook.py \"{book_dir}\"")


if __name__ == "__main__":
    main()
