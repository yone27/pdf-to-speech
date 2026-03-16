import base64
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, List, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

SOURCE_BOOK_DIR = "guion"
SOURCE_TXT_FALLBACK = None
IMAGE_MODEL_NAME = "gemini-2.5-flash-image"
IMAGES_FORMAT = "png"
REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 3
MAX_CONCURRENT_PER_PART = 5 
SKIP_EXISTING = True  # Si True, no re-genera imágenes que ya existen

# Configuración para Batch API de Gemini
USE_BATCH_API = False  # Mantener comportamiento actual por defecto
BATCH_SIZE = 80  # Número máximo de prompts por batch
MAX_BATCH_RETRIES = 3  # Reintentos si un batch completo falla
BATCH_POLL_INTERVAL = 10.0  # Segundos entre consultas de estado del batch
BATCH_TOTAL_TIMEOUT = 60 * 60.0  # Tiempo máximo total (en segundos) esperando un batch

# Descripción global de estilo para TODAS las imágenes (personalizable)
# Ejemplo: "children's book illustration style, soft pastel colors, watercolor, 4k, highly detailed"
GLOBAL_IMAGE_STYLE: str = """
Vintage agricultural handbook illustration style, watercolor and ink drawing.

Aged beige paper background with subtle paper texture.

Muted earthy colors, soft watercolor shading, delicate ink outlines.

Classic botanical and farming encyclopedia illustration aesthetic.

Clean educational diagram style with simple composition and clear visual elements.

no text
no captions
no typography
"""
# Configuración opcional de personaje recurrente (por ejemplo Paco la Patata)
# Capítulos (slugs de carpeta dentro de img/) donde debe aparecer el personaje.
# Por defecto se pensó en "cap 1", "cap 2", pero puedes adaptarlo a tus slugs reales.
CHARACTER_CHAPTERS: list[str] = [
    "cap 1",
    "cap 2",
]

# Descripción del personaje. Se añadirá como texto extra en los capítulos configurados.
# Ejemplo para Paco la Patata:
# "Paco la Patata, a friendly potato-shaped farmer character with big expressive eyes, straw hat, blue shirt, brown overalls and boots, vintage farming illustration style"
CHARACTER_DESCRIPTION: str = "Paco la Patata, a friendly potato-shaped farmer character with big expressive eyes, straw hat, blue shirt, brown overalls and boots, vintage farming illustration style"

# Palabras clave que indican que el prompt realmente muestra al personaje.
# Solo cuando el prompt contenga alguna de estas palabras se añadirá CHARACTER_DESCRIPTION.
CHARACTER_PROMPT_KEYWORDS: List[str] = [
  "paco la patata",
  "paco",
]

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


def build_gemini_batch_url(model_name: str, api_key: str) -> str:
  """
  Construye la URL para la Batch API de Gemini usando el mismo host base.
  Usa el endpoint REST documentado:
    POST https://generativelanguage.googleapis.com/v1beta/models/{model}:batchGenerateContent
  """
  base_url = os.getenv(
    "GEMINI_API_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta",
  )
  if not model_name.startswith("models/"):
    model_path = f"models/{model_name}"
  else:
    model_path = model_name
  return f"{base_url}/{model_path}:batchGenerateContent?key={api_key}"


def resolve_book_dir() -> Tuple[str, str]:
  if SOURCE_BOOK_DIR:
    book_dir = os.path.abspath(SOURCE_BOOK_DIR)
    book_name = os.path.basename(book_dir)
    return book_name, book_dir

  txt_path = os.path.abspath(SOURCE_TXT_FALLBACK)
  if not os.path.isfile(txt_path):
    raise FileNotFoundError(
      f"No se encontró el TXT de referencia: {txt_path}. "
      "Configura SOURCE_BOOK_DIR o corrige SOURCE_TXT_FALLBACK."
    )
  book_name = os.path.splitext(os.path.basename(txt_path))[0]
  base_dir = os.path.dirname(txt_path)
  book_dir = os.path.join(base_dir, book_name)
  return book_name, book_dir


def iter_prompt_files(book_dir: str) -> Iterable[Tuple[str, str, str]]:
  img_root = os.path.join(book_dir, "img")
  if not os.path.isdir(img_root):
    raise FileNotFoundError(
      f"No se encontró la carpeta 'img' en el libro: {img_root}. "
      "Primero ejecuta 02_parts_to_image_prompts.py."
    )

  for chapter_slug in sorted(os.listdir(img_root)):
    chapter_img_dir = os.path.join(img_root, chapter_slug)
    if not os.path.isdir(chapter_img_dir):
      continue

    for filename in sorted(os.listdir(chapter_img_dir)):
      if not filename.lower().endswith(".txt"):
        continue
      if not filename.startswith("part"):
        continue

      base_name, _ = os.path.splitext(filename)
      yield chapter_slug, base_name, os.path.join(chapter_img_dir, filename)


def generate_image_for_prompt(
  prompt: str,
  *,
  model_name: str,
  api_key: str,
  timeout: float = REQUEST_TIMEOUT,
  max_retries: int = MAX_RETRIES,
) -> bytes:
  url = build_gemini_url(model_name, api_key)

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
      for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and "data" in inline:
          b64 = inline["data"]
          return base64.b64decode(b64)

      raise RuntimeError("No se encontró 'inlineData' con imagen en la respuesta de Gemini.")

    except (requests.RequestException, RuntimeError, ValueError) as exc:
      last_error = exc
      if attempt < max_retries:
        wait_time = 2**attempt
        print(
          f"  [WARN] Error al generar imagen (intento {attempt}/{max_retries}): {exc}. "
          f"Reintentando en {wait_time}s...",
          file=sys.stderr,
        )
        time.sleep(wait_time)
      else:
        break

  raise RuntimeError(f"Falló la generación de imagen tras {max_retries} intentos: {last_error}")


def batch_generate_images_for_prompts(
  prompts: List[str],
  *,
  model_name: str,
  api_key: str,
  max_retries: int = MAX_BATCH_RETRIES,
) -> List[bytes | None]:
  """
  Llama a la Batch API de Gemini (models.batchGenerateContent) para un conjunto de prompts.

  - Crea un recurso GenerateContentBatch con peticiones "inlined".
  - Hace polling de la operación hasta que termina o se agota BATCH_TOTAL_TIMEOUT.
  - Devuelve una lista de la misma longitud que `prompts` con los bytes de cada imagen
    (o None si no se pudo extraer la imagen para ese prompt en concreto).
  """
  url = build_gemini_batch_url(model_name, api_key)

  # Normalizamos el nombre del modelo para ponerlo también en el cuerpo del batch,
  # siguiendo el esquema de GenerateContentBatch.
  if not model_name.startswith("models/"):
    model_path = f"models/{model_name}"
  else:
    model_path = model_name

  payload = {
    "batch": {
      "model": model_path,
      "displayName": f"image-batch-{int(time.time())}",
      "inputConfig": {
        "requests": {
          "requests": [
            {
              "request": {
                "contents": [
                  {
                    "parts": [
                      {"text": prompt},
                    ]
                  }
                ]
              },
              "metadata": {"index": idx},
            }
            for idx, prompt in enumerate(prompts)
          ]
        }
      },
    }
  }

  headers = {
    "Content-Type": "application/json",
  }

  last_error: Exception | None = None

  for attempt in range(1, max_retries + 1):
    try:
      # 1) Crear el batch (obtendremos una Operation)
      response = requests.post(
        url,
        headers=headers,
        data=json.dumps(payload),
        timeout=REQUEST_TIMEOUT,
      )
      if not response.ok:
        raise RuntimeError(
          f"Respuesta no exitosa de Gemini Batch (status={response.status_code}): {response.text[:500]}"
        )

      data = response.json()
      operation_name = data.get("name")
      if not operation_name:
        raise RuntimeError("La respuesta de batch no contiene 'name' de la operación.")

      # 2) Polling de la operación hasta que termine o se agote el timeout total
      base_url = os.getenv(
        "GEMINI_API_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
      )
      operation_url = f"{base_url}/{operation_name}?key={api_key}"

      start_time = time.time()

      while True:
        if time.time() - start_time > BATCH_TOTAL_TIMEOUT:
          raise RuntimeError(
            f"Timeout esperando a que termine la operación de batch ({BATCH_TOTAL_TIMEOUT}s)."
          )

        op_resp = requests.get(operation_url, timeout=REQUEST_TIMEOUT)
        if not op_resp.ok:
          raise RuntimeError(
            f"Error al leer estado de la operación de batch (status={op_resp.status_code}): "
            f"{op_resp.text[:500]}"
          )

        op_data = op_resp.json()
        if not op_data.get("done"):
          time.sleep(BATCH_POLL_INTERVAL)
          continue

        # Si hay error en la operación, lo propagamos
        if op_data.get("error"):
          raise RuntimeError(f"Error en operación de batch: {op_data['error']}")

        # 3) Extraemos las respuestas inlined del GenerateContentBatch
        batch = op_data.get("response") or {}
        output = batch.get("output") or {}
        responses_file = output.get("responsesFile")

        images: List[bytes | None] = []

        if responses_file:
          # Caso principal: las respuestas vienen en un fichero JSONL.
          # Descargamos el archivo desde la API de Files.
          file_url = f"{base_url}/{responses_file}?alt=media&key={api_key}"
          file_resp = requests.get(file_url, timeout=BATCH_TOTAL_TIMEOUT)
          if not file_resp.ok:
            raise RuntimeError(
              f"Error al descargar responsesFile (status={file_resp.status_code}): "
              f"{file_resp.text[:500]}"
            )

          lines = [ln for ln in file_resp.text.splitlines() if ln.strip()]
          if len(lines) != len(prompts):
            raise RuntimeError(
              f"Número de líneas en responsesFile ({len(lines)}) != número de prompts ({len(prompts)})."
            )

          for line in lines:
            resp_obj = json.loads(line)
            candidates = (resp_obj.get("candidates") or [])
            if not candidates:
              images.append(None)
              continue

            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []

            found_bytes: bytes | None = None
            for part in parts:
              inline = part.get("inlineData") or part.get("inline_data")
              if inline and "data" in inline:
                b64 = inline["data"]
                found_bytes = base64.b64decode(b64)
                break

            images.append(found_bytes)

          return images

        # Fallback: respuestas inline (menos habitual, pero soportado por la API).
        inlined_wrapper = output.get("inlinedResponses") or {}
        inlined_responses = inlined_wrapper.get("inlinedResponses") or []

        if len(inlined_responses) != len(prompts):
          raise RuntimeError(
            f"Número de respuestas de batch ({len(inlined_responses)}) != número de prompts ({len(prompts)})."
          )

        for item in inlined_responses:
          resp = item.get("response") or {}
          candidates = resp.get("candidates") or []
          if not candidates:
            images.append(None)
            continue

          content = candidates[0].get("content") or {}
          parts = content.get("parts") or []

          found_bytes = None
          for part in parts:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and "data" in inline:
              b64 = inline["data"]
              found_bytes = base64.b64decode(b64)
              break

          images.append(found_bytes)

        return images

    except (requests.RequestException, RuntimeError, ValueError) as exc:
      last_error = exc
      if attempt < max_retries:
        wait_time = 2**attempt
        print(
          f"  [WARN] Error en batchGenerateContent (intento {attempt}/{max_retries}): {exc}. "
          f"Reintentando en {wait_time}s...",
          file=sys.stderr,
        )
        time.sleep(wait_time)
      else:
        break

  raise RuntimeError(f"Falló la generación batch tras {max_retries} intentos: {last_error}")


def build_final_prompt(
  chapter_slug: str,
  base_name: str,
  prompt: str,
  img_filename: str,
) -> str:
  """
  Devuelve el texto final que se envía al modelo de imágenes,
  aplicando estilo global y personaje recurrente si corresponde.
  """
  final_prompt = prompt

  if GLOBAL_IMAGE_STYLE:
    final_prompt = f"{GLOBAL_IMAGE_STYLE}\n\n{final_prompt}"

  if (
    CHARACTER_DESCRIPTION
    and chapter_slug in CHARACTER_CHAPTERS
    and any(
      keyword.lower() in prompt.lower()
      for keyword in CHARACTER_PROMPT_KEYWORDS
    )
  ):
    final_prompt = f"{final_prompt}\n\n{CHARACTER_DESCRIPTION}"
    print(f"    [INFO] Añadiendo personaje al prompt de {img_filename}")

  return final_prompt


def _process_prompt_to_image(
  chapter_slug: str,
  base_name: str,
  idx: int,
  prompt: str,
  img_path: str,
  img_filename: str,
  api_key: str,
) -> None:
  final_prompt = build_final_prompt(
    chapter_slug=chapter_slug,
    base_name=base_name,
    prompt=prompt,
    img_filename=img_filename,
  )

  image_bytes = generate_image_for_prompt(
    final_prompt,
    model_name=IMAGE_MODEL_NAME,
    api_key=api_key,
  )

  with open(img_path, "wb") as img_file:
    img_file.write(image_bytes)


def generate_images_batch_for_jobs(
  chapter_slug: str,
  base_name: str,
  jobs: List[Tuple[int, str, str, str]],
  api_key: str,
) -> None:
  """
  Procesa una lista de jobs usando la Batch API.

  Cada job es (idx, prompt, img_path, img_filename).
  Se generan prompts finales (con estilo global y personaje) y se envían en batches
  de tamaño BATCH_SIZE. Cada batch se procesa con batchGenerateContent.

  Si un batch completo falla o alguna respuesta viene sin imagen, se lanza un error
  y se detiene la ejecución (no se hace fallback a modo individual).
  """
  total_jobs = len(jobs)
  print(f"  [INFO] Generando {total_jobs} imágenes en modo batch (BATCH_SIZE={BATCH_SIZE})...")

  if total_jobs == 0:
    return

  for start_idx in range(0, total_jobs, BATCH_SIZE):
    batch_jobs = jobs[start_idx : start_idx + BATCH_SIZE]
    batch_prompts: List[str] = []
    for _, prompt, _, img_filename in batch_jobs:
      final_prompt = build_final_prompt(
        chapter_slug=chapter_slug,
        base_name=base_name,
        prompt=prompt,
        img_filename=img_filename,
      )
      batch_prompts.append(final_prompt)

    batch_number = start_idx // BATCH_SIZE + 1
    total_batches = (total_jobs + BATCH_SIZE - 1) // BATCH_SIZE
    print(
      f"  [INFO] Procesando batch {batch_number}/{total_batches} "
      f"con {len(batch_jobs)} prompts..."
    )

    try:
      images_bytes_list = batch_generate_images_for_prompts(
        batch_prompts,
        model_name=IMAGE_MODEL_NAME,
        api_key=api_key,
      )
    except RuntimeError as exc:
      print(
        f"  [WARN] Falló batch {batch_number}/{total_batches}: {exc}.",
        file=sys.stderr,
      )
      # En modo batch puro, propagamos el error para que falle el script.
      raise

    for (idx, prompt, img_path, img_filename), image_bytes in zip(
      batch_jobs, images_bytes_list
    ):
      if image_bytes is None:
        print(
          f"  [WARN] Respuesta inválida o sin imagen para {img_filename} en batch.",
          file=sys.stderr,
        )
        # Si alguna imagen falla en batch, detenemos la ejecución.
        raise RuntimeError(
          f"Respuesta inválida o sin imagen para {img_filename} en batch."
        )

      with open(img_path, "wb") as img_file:
        img_file.write(image_bytes)


def main() -> None:
  book_name, book_dir = resolve_book_dir()
  print(f"Libro: {book_name}")
  print(f"Carpeta base del libro: {book_dir}")

  api_key = load_api_key()

  prompt_files = list(iter_prompt_files(book_dir))
  if not prompt_files:
    print("No se encontraron archivos de prompts en img/<chapter_slug>/partNNN.txt.", file=sys.stderr)
    return

  total_files = len(prompt_files)
  print(f"Archivos de prompts encontrados: {total_files}")

  for file_idx, (chapter_slug, base_name, prompts_path) in enumerate(prompt_files, start=1):
    chapter_img_dir = os.path.join(book_dir, "img", chapter_slug)

    print(f"[{file_idx}/{total_files}] Procesando prompts de {chapter_slug}/{base_name}.txt...")

    with open(prompts_path, "r", encoding="utf-8") as f:
      prompts = [ln.strip() for ln in f if ln.strip()]

    if not prompts:
      print("  No hay prompts en el archivo, se omite.")
      continue

    jobs: list[tuple[int, str, str, str]] = []

    for idx, prompt in enumerate(prompts, start=1):
      img_filename = f"{base_name}_img{idx:02d}.{IMAGES_FORMAT}"
      img_path = os.path.join(chapter_img_dir, img_filename)

      if SKIP_EXISTING and os.path.isfile(img_path):
        print(f"  - Imagen {img_filename} ya existe, se omite (SKIP_EXISTING=True).")
        continue

      print(f"  - Generando imagen {idx}/{len(prompts)} para {base_name}...")
      jobs.append((idx, prompt, img_path, img_filename))

    if not jobs:
      continue

    if USE_BATCH_API:
      print("  [INFO] Usando Batch API para este archivo de prompts.")
      generate_images_batch_for_jobs(
        chapter_slug=chapter_slug,
        base_name=base_name,
        jobs=jobs,
        api_key=api_key,
      )
    else:
      max_workers = max(1, int(MAX_CONCURRENT_PER_PART))

      with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
          executor.submit(
            _process_prompt_to_image,
            chapter_slug,
            base_name,
            idx,
            prompt,
            img_path,
            img_filename,
            api_key,
          )
          for idx, prompt, img_path, img_filename in jobs
        ]

        for future in futures:
          future.result()

  print("✅ Imágenes generadas a partir de los prompts. "
        "Estructura: img/<chapter_slug>/partNNN_img01.png, partNNN_img02.png, ...")


if __name__ == "__main__":
  main()