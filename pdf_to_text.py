import os
import re
import argparse
import pdfplumber

MAX_CHARS_DEFAULT = 1500


def extract_text_from_pdf(path: str) -> str:
    full_text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
    return full_text


def clean_text(text: str) -> str:
    text = re.sub(r"\n+", "\n", text)
    text = re.sub(r" +", " ", text)
    return text.strip()


def chunk_text(text: str, max_chars: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?]) +", text)
    chunks: list[str] = []
    current_chunk = ""

    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 <= max_chars:
            current_chunk += sentence + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence + " "

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def get_book_dirs(pdf_path: str, base_output: str | None = None) -> tuple[str, str, str]:
    pdf_path = os.path.abspath(pdf_path)
    book_name = os.path.splitext(os.path.basename(pdf_path))[0]

    if base_output:
        base_dir = os.path.abspath(base_output)
    else:
        base_dir = os.path.dirname(pdf_path)

    book_dir = os.path.join(base_dir, book_name)
    text_dir = os.path.join(book_dir, "text")
    return book_name, book_dir, text_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convierte un PDF en partes de texto (part001.txt, ...) para revisión manual."
    )
    parser.add_argument("pdf_path", help="Ruta al archivo PDF de entrada.")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=MAX_CHARS_DEFAULT,
        help=f"Número máximo de caracteres por parte (por defecto {MAX_CHARS_DEFAULT}).",
    )
    parser.add_argument(
        "--output-dir",
        help="Directorio base donde crear la carpeta del libro. "
        "Por defecto se usa el directorio del propio PDF.",
    )

    args = parser.parse_args()

    if not os.path.isfile(args.pdf_path):
        raise FileNotFoundError(f"No se encontró el PDF: {args.pdf_path}")

    book_name, book_dir, text_dir = get_book_dirs(args.pdf_path, args.output_dir)
    os.makedirs(text_dir, exist_ok=True)

    print(f"Libro detectado: {book_name}")
    print(f"Carpeta de texto: {text_dir}")

    print("Extrayendo texto del PDF...")
    text = extract_text_from_pdf(args.pdf_path)

    print("Limpiando texto...")
    text = clean_text(text)

    print("Dividiendo en partes...")
    chunks = chunk_text(text, args.max_chars)
    print(f"Total de partes: {len(chunks)}")

    for i, chunk in enumerate(chunks, start=1):
        file_name = f"part{str(i).zfill(3)}.txt"
        file_path = os.path.join(text_dir, file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(chunk)

    print(f"✅ Partes de texto guardadas en: {text_dir}")


if __name__ == "__main__":
    main()

