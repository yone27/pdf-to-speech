import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Iterable, List, Tuple
import requests
from dotenv import load_dotenv

load_dotenv()

BOOK_DIR = "jardin"
NUM_IMAGES_PER_PART = 10
MODEL_NAME_TEXT = "gemini-2.5-pro"
NUM_WORKERS = 3  # 1 = secuencial; >1 = procesamiento en paralelo

PROMPT_TEMPLATE = (
  "Genera una lista de prompts breves para un modelo de generación de imágenes, "
  "basados en el siguiente fragmento de una historia. Quiero EXACTAMENTE "
  "{num_images} prompts.\n\n"
  "Requisitos:\n"
  "- Cada prompt debe ir en una línea separada.\n"
  "- No incluyas numeración ni viñetas, solo el texto del prompt.\n"
  "- Describe escenas visuales variadas y coherentes con el texto.\n\n"
  "Texto:\n"
  "{part_text}"
)

REQUEST_TIMEOUT = 60.0
MAX_RETRIES = 3
SKIP_EXISTING = True  # Si True, no vuelve a generar prompts si ya existe el archivo destino


def load_api_key() -> str:
  api_key = os.getenv("GEMINI_API_KEY")
  if not api_key:
    raise RuntimeError(
      "No se encontró la variable de entorno GEMINI_API_KEY. "
      "Configúrala con tu clave de la API de Gemini."
    )
  return api_key


def build_gemini_url(model_name: str, api_key: str) -> str:
  base_url = os.getenv(
    "GEMINI_API_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta",
  )
  if not model_name.startswith("models/"):
    model_path = f"models/{model_name}"
  else:
    model_path = model_name
  return f"{base_url}/{model_path}:generateContent?key={api_key}"


def get_book_dirs(book_path: str) -> Tuple[str, str, str, str]:
  book_dir = os.path.abspath(book_path)
  book_name = os.path.basename(book_dir)
  text_dir = os.path.join(book_dir, "text")
  img_root_dir = os.path.join(book_dir, "img")

  if not os.path.isdir(book_dir):
    raise FileNotFoundError(
      f"No se encontró la carpeta del libro: {book_dir}. "
      "Asegúrate de que BOOK_DIR apunte a la carpeta generada por 01_text_to_parts_hier.py."
    )
  if not os.path.isdir(text_dir):
    raise FileNotFoundError(
      f"No se encontró la carpeta de texto del libro: {text_dir}. "
      "Primero ejecuta 01_text_to_parts_hier.py."
    )

  os.makedirs(img_root_dir, exist_ok=True)
  return book_name, book_dir, text_dir, img_root_dir


def call_gemini_for_prompts(
  part_text: str,
  *,
  num_images: int,
  model_name: str,
  api_key: str,
  prompt_template: str = PROMPT_TEMPLATE,
  timeout: float = REQUEST_TIMEOUT,
  max_retries: int = MAX_RETRIES,
) -> str:
  url = build_gemini_url(model_name, api_key)
  prompt = prompt_template.format(num_images=num_images, part_text=part_text)

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
        raise RuntimeError(
          f"Respuesta no exitosa de Gemini (status={response.status_code}): {response.text[:500]}"
        )

      data = response.json()
      candidates = data.get("candidates") or []
      if not candidates:
        raise RuntimeError("La respuesta de Gemini no contiene 'candidates'.")

      content = candidates[0].get("content") or {}
      parts = content.get("parts") or []
      if not parts or "text" not in parts[0]:
        raise RuntimeError("No se encontró texto en la respuesta de Gemini.")

      return parts[0]["text"]

    except (requests.RequestException, RuntimeError, ValueError) as exc:
      last_error = exc
      if attempt < max_retries:
        wait_time = 2**attempt
        print(
          f"  [WARN] Error al pedir prompts (intento {attempt}/{max_retries}): {exc}. "
          f"Reintentando en {wait_time}s...",
          file=sys.stderr,
        )
        time.sleep(wait_time)
      else:
        break

  raise RuntimeError(f"Falló la generación de prompts tras {max_retries} intentos: {last_error}")


def normalize_prompts(raw_text: str, expected_count: int) -> List[str]:
  lines = [ln.strip() for ln in raw_text.splitlines()]
  cleaned: List[str] = []
  for ln in lines:
    if not ln:
      continue
    ln = re.sub(r"^[\-\*\d\.\)\s]+", "", ln)
    if ln:
      cleaned.append(ln)

  if len(cleaned) >= expected_count:
    return cleaned[:expected_count]

  if cleaned:
    while len(cleaned) < expected_count:
      cleaned.append(cleaned[-1])
  else:
    cleaned = [f"Imagen {i+1}" for i in range(expected_count)]

  return cleaned


def iter_parts(text_dir: str) -> Iterable[Tuple[str, str, str]]:
  for chapter_slug in sorted(os.listdir(text_dir)):
    chapter_dir = os.path.join(text_dir, chapter_slug)
    if not os.path.isdir(chapter_dir):
      continue

    for filename in sorted(os.listdir(chapter_dir)):
      if not filename.lower().endswith(".txt"):
        continue
      if not filename.startswith("part"):
        continue

      yield chapter_slug, filename, os.path.join(chapter_dir, filename)


_print_lock = Lock()


def _process_one_part(
  index: int,
  total: int,
  chapter_slug: str,
  filename: str,
  part_path: str,
  prompts_path: str,
  api_key: str,
) -> Tuple[int, str, str, bool, str | None]:
  try:
    with _print_lock:
      print(f"[{index}/{total}] Generando prompts para {chapter_slug}/{filename}...")

    with open(part_path, "r", encoding="utf-8") as f:
      part_text = f.read()

    if not part_text.strip():
      prompts = [f"Escena vacía {i+1}" for i in range(NUM_IMAGES_PER_PART)]
    else:
      raw = call_gemini_for_prompts(
        part_text,
        num_images=NUM_IMAGES_PER_PART,
        model_name=MODEL_NAME_TEXT,
        api_key=api_key,
        prompt_template=PROMPT_TEMPLATE,
      )
      prompts = normalize_prompts(raw, NUM_IMAGES_PER_PART)

    os.makedirs(os.path.dirname(prompts_path), exist_ok=True)
    with open(prompts_path, "w", encoding="utf-8") as f_out:
      for prompt in prompts:
        f_out.write(prompt + "\n")

    return (index, chapter_slug, filename, True, None)
  except Exception as e:
    return (index, chapter_slug, filename, False, str(e))


def main() -> None:
  book_name, book_dir, text_dir, img_root_dir = get_book_dirs(BOOK_DIR)

  print(f"Libro: {book_name}")
  print(f"Carpeta base del libro: {book_dir}")
  print(f"Carpeta de texto: {text_dir}")
  print(f"Carpeta raíz de imágenes (prompts): {img_root_dir}")
  print(f"Hilos: {NUM_WORKERS}")

  api_key = load_api_key()

  parts = list(iter_parts(text_dir))
  if not parts:
    print("No se encontraron partes en la carpeta de texto. ¿Ejecutaste 01_text_to_parts_hier.py?", file=sys.stderr)
    return

  total = len(parts)
  print(f"Partes totales encontradas: {total}")

  jobs: List[Tuple[int, str, str, str, str]] = []
  for index, (chapter_slug, filename, part_path) in enumerate(parts, start=1):
    chapter_img_dir = os.path.join(img_root_dir, chapter_slug)
    base_name, _ = os.path.splitext(filename)
    prompts_path = os.path.join(chapter_img_dir, f"{base_name}.txt")

    if SKIP_EXISTING and os.path.isfile(prompts_path):
      with _print_lock:
        print(f"[{index}/{total}] {chapter_slug}/{filename}: ya existe, se omite (SKIP_EXISTING=True).")
      continue

    jobs.append((index, chapter_slug, filename, part_path, prompts_path))

  if not jobs:
    print("✅ No hay partes pendientes (todas ya existían).")
    return

  if NUM_WORKERS <= 1:
    for (index, chapter_slug, filename, part_path, prompts_path) in jobs:
      _process_one_part(index, total, chapter_slug, filename, part_path, prompts_path, api_key)
  else:
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
      futures = {
        executor.submit(
          _process_one_part,
          index, total, chapter_slug, filename, part_path, prompts_path, api_key,
        ): (index, chapter_slug, filename)
        for (index, chapter_slug, filename, part_path, prompts_path) in jobs
      }
      errors: List[str] = []
      for future in as_completed(futures):
        index, chapter_slug, filename, ok, err = future.result()
        if not ok:
          errors.append(f"[{index}/{total}] {chapter_slug}/{filename}: {err}")
      if errors:
        for msg in errors:
          print(msg, file=sys.stderr)
        sys.exit(1)

  print("✅ Prompts de imagen generados en subcarpetas de 'img/<chapter_slug>/partNNN.txt'.")


if __name__ == "__main__":
  main()