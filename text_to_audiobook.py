import os
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from google.cloud import texttospeech


VOICE_NAME = "Enceladus"
MODEL_NAME = "gemini-2.5-pro-tts"
LANGUAGE_CODE = "es-419"  # Español Latinoamérica, como en el playground
DEFAULT_WORKERS = 1
DEFAULT_PROMPT = "Lee el siguiente texto en español de forma natural."


load_dotenv()


def synthesize(prompt: str, text: str, output_filepath: str) -> None:
    """Sintetiza voz desde el texto y la guarda en un MP3."""
    client = texttospeech.TextToSpeechClient()

    synthesis_input = texttospeech.SynthesisInput(
        text=text, 
        prompt=prompt,
        )

    voice = texttospeech.VoiceSelectionParams(
        language_code=LANGUAGE_CODE,
        name=VOICE_NAME,
        model_name=MODEL_NAME,
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=22050,
        speaking_rate=1.0,
        volume_gain_db=0.0,
        
    )

    response = client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )

    with open(output_filepath, "wb") as out:
        out.write(response.audio_content)


def resolve_book_dirs(book_arg: str, base_output: str | None = None) -> tuple[str, str, str, str]:
    """Resuelve nombre de libro y carpetas de texto/audio a partir del argumento."""
    path = os.path.abspath(book_arg)

    if os.path.isdir(path):
        book_dir = path
        book_name = os.path.basename(book_dir)
    else:
        if base_output:
            base_dir = os.path.abspath(base_output)
        else:
            base_dir = os.getcwd()
        book_name = book_arg
        book_dir = os.path.join(base_dir, book_name)

    text_dir = os.path.join(book_dir, "text")
    audio_dir = os.path.join(book_dir, "audio")
    return book_name, book_dir, text_dir, audio_dir


def collect_parts(text_dir: str, audio_dir: str) -> list[tuple[str, str]]:
    """Devuelve una lista de (ruta_txt, ruta_mp3) ordenada."""
    if not os.path.isdir(text_dir):
        raise FileNotFoundError(f"No existe la carpeta de texto: {text_dir}")

    files = [f for f in os.listdir(text_dir) if f.lower().endswith(".txt")]
    files.sort()

    tasks: list[tuple[str, str]] = []
    for fname in files:
        input_path = os.path.join(text_dir, fname)
        base, _ = os.path.splitext(fname)
        output_name = f"{base}.wav"
        output_path = os.path.join(audio_dir, output_name)
        tasks.append((input_path, output_path))

    return tasks


def process_part(prompt: str, input_path: str, output_path: str) -> str:
    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()

    synthesize(prompt, text, output_path)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Genera audiolibro a partir de ficheros de texto partXXX.txt "
            "usando Gemini-TTS (Cloud Text-to-Speech)."
        )
    )
    parser.add_argument(
        "book",
        help=(
            "Nombre de la carpeta del libro (p.ej. 'el-muro') "
            "o ruta absoluta/relativa a dicha carpeta."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Número máximo de peticiones concurrentes (por defecto {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt/estilo para la síntesis de voz.",
    )
    parser.add_argument(
        "--base-dir",
        help=(
            "Directorio base donde se encuentra la carpeta del libro "
            "cuando se pasa solo el nombre."
        ),
    )

    args = parser.parse_args()

    book_name, book_dir, text_dir, audio_dir = resolve_book_dirs(
        args.book, args.base_dir
    )

    os.makedirs(audio_dir, exist_ok=True)

    print(f"Libro: {book_name}")
    print(f"Carpeta texto: {text_dir}")
    print(f"Carpeta audio: {audio_dir}")

    tasks = collect_parts(text_dir, audio_dir)
    total = len(tasks)

    if total == 0:
        print("No se encontraron partes de texto para procesar.")
        return

    print(f"Generando audio para {total} partes con {args.workers} workers...")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_part, args.prompt, in_path, out_path): in_path
            for in_path, out_path in tasks
        }

        for future in as_completed(futures):
            input_path = futures[future]
            try:
                output_path = future.result()
                print(f"✅ Audio generado: {output_path}")
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ Error procesando {input_path}: {exc}")

    print("✔️ Proceso de generación de audio finalizado.")


if __name__ == "__main__":
    main()

