import argparse
import json
import os
import sys
import time
from typing import Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv


# Cargar variables de entorno desde .env (incluye GEMINI_API_KEY)
load_dotenv()


# Configuración por defecto: puedes ejecutar el script sin argumentos
SOURCE_BOOK_DIR = "odiseaES"
TARGET_LANG_CODE = "de"
MODEL_NAME = "gemini-2.5-pro"

# Comportamiento adicional
SKIP_EXISTING = True  # Si True, no vuelve a traducir archivos ya generados en la carpeta destino
REQUEST_TIMEOUT = 60.0
MAX_RETRIES = 3
DEFAULT_WORKERS = 2


class GeminiTranslationError(Exception):
    pass


def load_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No se encontró la variable de entorno GEMINI_API_KEY. "
            "Configúrala con tu clave de la API de Gemini."
        )
    return api_key


def resolve_book_dirs(source_book_dir: str, target_lang: str) -> Tuple[str, str, str, str]:
    """
    Dado el directorio de un libro (ej: atalntisES), devuelve:
    - src_book_dir: ruta absoluta del libro origen
    - src_text_dir: carpeta `text` del libro origen
    - dst_book_dir: ruta absoluta de la carpeta hermana con sufijo de idioma (ej: atalntisES-en)
    - dst_text_dir: carpeta `text` dentro del libro destino
    """
    src_book_dir = os.path.abspath(source_book_dir)
    src_text_dir = os.path.join(src_book_dir, "text")

    if not os.path.isdir(src_text_dir):
        raise FileNotFoundError(f"No se encontró la carpeta de texto de origen: {src_text_dir}")

    dst_book_dir = f"{src_book_dir}-{target_lang}"
    dst_text_dir = os.path.join(dst_book_dir, "text")

    os.makedirs(dst_text_dir, exist_ok=True)

    return src_book_dir, src_text_dir, dst_book_dir, dst_text_dir


def build_gemini_url(model_name: str, api_key: str) -> str:
    """
    Construye la URL del endpoint de Gemini para generateContent.
    """
    base_url = os.getenv(
        "GEMINI_API_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    )
    # Asegurar que el modelo tenga el prefijo correcto "models/"
    if not model_name.startswith("models/"):
        model_path = f"models/{model_name}"
    else:
        model_path = model_name
    return f"{base_url}/{model_path}:generateContent?key={api_key}"


def translate_text_with_gemini(
    text: str,
    target_lang: str,
    *,
    model_name: str,
    api_key: str,
    timeout: float = REQUEST_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> str:
    """
    Envía el texto a la API de Gemini para traducirlo al idioma `target_lang`
    y devuelve solo el texto traducido.
    """
    url = build_gemini_url(model_name, api_key)

    prompt = (
        f"Traduce el siguiente texto al idioma '{target_lang}'. "
        "Devuelve únicamente la traducción, sin explicaciones ni comentarios adicionales.\n\n"
        f"{text}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                data=json.dumps(payload),
                timeout=timeout,
            )
            if not response.ok:
                raise GeminiTranslationError(
                    f"Respuesta no exitosa de Gemini (status={response.status_code}): {response.text[:500]}"
                )

            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise GeminiTranslationError("La respuesta de Gemini no contiene 'candidates'.")

            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []
            if not parts or "text" not in parts[0]:
                raise GeminiTranslationError("No se encontró texto en la respuesta de Gemini.")

            return parts[0]["text"]

        except (requests.RequestException, GeminiTranslationError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries:
                wait_time = 2**attempt
                print(
                    f"  [WARN] Error al traducir (intento {attempt}/{max_retries}): {exc}. "
                    f"Reintentando en {wait_time}s...",
                    file=sys.stderr,
                )
                time.sleep(wait_time)
            else:
                break

    raise GeminiTranslationError(f"Falló la traducción tras {max_retries} intentos: {last_error}")


def process_file(
    index: int,
    total: int,
    filename: str,
    src_path: str,
    dst_path: str,
    *,
    target_lang: str,
    model_name: str,
    api_key: str,
    skip_existing: bool,
) -> dict:
    """
    Traduce un solo archivo de texto y devuelve un dict con el estado
    de la operación: ok / skipped / error, e información adicional.
    """
    result: dict = {
        "status": "ok",
        "filename": filename,
        "src_path": src_path,
        "dst_path": dst_path,
        "made_request": False,
        "error": None,
    }

    try:
        if skip_existing and os.path.isfile(dst_path):
            result["status"] = "skipped"
            return result

        with open(src_path, "r", encoding="utf-8") as f:
            original_text = f.read()

        if not original_text.strip():
            translated_text = ""
        else:
            translated_text = translate_text_with_gemini(
                original_text,
                target_lang,
                model_name=model_name,
                api_key=api_key,
            )
            result["made_request"] = True

        with open(dst_path, "w", encoding="utf-8") as f_out:
            f_out.write(translated_text)

        result["status"] = "ok"
        return result
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(exc)
        return result


def process_files(
    src_text_dir: str,
    dst_text_dir: str,
    *,
    target_lang: str,
    model_name: str,
    api_key: str,
    skip_existing: bool,
    workers: int,
) -> None:
    """
    Recorre todos los .txt en src_text_dir, traduce su contenido y guarda
    los resultados en dst_text_dir con el mismo nombre de archivo.
    """
    txt_files = sorted(
        f for f in os.listdir(src_text_dir) if f.lower().endswith(".txt")
    )

    if not txt_files:
        print(f"No se encontraron archivos .txt en {src_text_dir}")
        return

    total = len(txt_files)
    translated = 0
    skipped = 0
    errors = 0
    total_requests = 0

    print(f"Archivos a procesar: {total}")

    # Crear tareas para cada archivo
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for index, filename in enumerate(txt_files, start=1):
            src_path = os.path.join(src_text_dir, filename)
            dst_path = os.path.join(dst_text_dir, filename)
            futures[
                executor.submit(
                    process_file,
                    index,
                    total,
                    filename,
                    src_path,
                    dst_path,
                    target_lang=target_lang,
                    model_name=model_name,
                    api_key=api_key,
                    skip_existing=skip_existing,
                )
            ] = (index, filename)

        for future in as_completed(futures):
            index, filename = futures[future]
            try:
                result = future.result()
                status = result.get("status")
                made_request = bool(result.get("made_request"))

                if status == "ok":
                    translated += 1
                    if made_request:
                        total_requests += 1
                    print(f"[{index}/{total}] OK '{filename}'")
                elif status == "skipped":
                    skipped += 1
                    print(f"[{index}/{total}] Saltando '{filename}' (ya existe en destino).")
                else:
                    errors += 1
                    if made_request:
                        total_requests += 1
                    error_msg = result.get("error") or "Error desconocido"
                    print(
                        f"[{index}/{total}] ERROR '{filename}': {error_msg}",
                        file=sys.stderr,
                    )
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(
                    f"[{index}/{total}] ERROR inesperado en '{filename}': {exc}",
                    file=sys.stderr,
                )

    print("\nResumen de traducción:")
    print(f"  Total archivos:         {total}")
    print(f"  Traducidos ok:          {translated}")
    print(f"  Saltados (existen):     {skipped}")
    print(f"  Con errores:            {errors}")
    print(f"  Peticiones reales a API:{total_requests}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Traduce todos los .txt de la carpeta 'text' de un libro "
            "usando la API de Gemini y guarda los resultados en una "
            "carpeta hermana con sufijo de idioma."
        )
    )
    parser.add_argument(
        "--book-dir",
        type=str,
        default=SOURCE_BOOK_DIR,
        help=(
            "Ruta al directorio del libro origen (el que contiene 'text/'). "
            "Por defecto se usa el valor de SOURCE_BOOK_DIR."
        ),
    )
    parser.add_argument(
        "--lang",
        type=str,
        default=TARGET_LANG_CODE,
        help=(
            "Código de idioma destino (ej: 'en', 'fr', 'de'). "
            "Por defecto se usa TARGET_LANG_CODE."
        ),
    )
    parser.add_argument(
        "--model",
        type=str,
        default=MODEL_NAME,
        help="Nombre del modelo de Gemini a usar. Ej: 'models/gemini-1.5-flash'.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Si se indica, sobrescribe archivos ya existentes en la carpeta destino.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Número máximo de peticiones concurrentes a Gemini (por defecto {DEFAULT_WORKERS}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        api_key = load_api_key()
    except RuntimeError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        src_book_dir, src_text_dir, dst_book_dir, dst_text_dir = resolve_book_dirs(
            args.book_dir,
            args.lang,
        )
    except FileNotFoundError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        sys.exit(1)

    skip_existing = not args.overwrite if SKIP_EXISTING else False

    print(f"Libro origen:   {src_book_dir}")
    print(f"Carpeta origen: {src_text_dir}")
    print(f"Libro destino:  {dst_book_dir}")
    print(f"Carpeta destino:{dst_text_dir}")
    print(f"Idioma destino: {args.lang}")
    print(f"Modelo Gemini:  {args.model}")
    print(f"Sobrescribir existentes: {'sí' if not skip_existing else 'no'}")
    print(f"Workers (hilos): {args.workers}")
    print()

    process_files(
        src_text_dir,
        dst_text_dir,
        target_lang=args.lang,
        model_name=args.model,
        api_key=api_key,
        skip_existing=skip_existing,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()

