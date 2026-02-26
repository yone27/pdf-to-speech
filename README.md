## Conversor de PDF a Audiolibro con Gemini TTS

Convierte un PDF en un **audiolibro** usando **Gemini 2.5 Pro TTS** en dos pasos:

1. PDF ➜ texto troceado (`partXXX.txt`).
2. Texto ➜ audio (`.wav` por parte).

## Requisitos rápidos

- Python 3.10+.
- Paquetes de Python:

```bash
pip install pdfplumber python-dotenv google-cloud-texttospeech
```

- Google Cloud con:
  - Proyecto creado.
  - API **Cloud Text-to-Speech** habilitada.
  - Credenciales mediante **gcloud** (lo que tú usas):

    ```powershell
    gcloud init
    gcloud auth application-default login
    ```
---

## Uso básico

Suponiendo que estás en la carpeta del proyecto y tienes `mi-libro.pdf`.

### 1. PDF ➜ texto (`pdf_to_text.py`)

```powershell
py pdf_to_text.py mi-libro.pdf
```

Hace esto:

- Crea `mi-libro/text/`.
- Extrae, limpia y trocea el texto del PDF.
- Genera `mi-libro/text/part001.txt`, `part002.txt`, ...

Revisa/edita esos `.txt` antes de generar el audio si quieres corregir algo.

### 2. Texto ➜ audio (`text_to_audiobook.py`)

```powershell
py text_to_audiobook.py mi-libro
```

Hace esto:

- Lee todos los `mi-libro/text/partXXX.txt`.
- Llama a Gemini 2.5 Pro TTS con la voz `Enceladus` (es-419).
- Crea `mi-libro/audio/partXXX.wav`.

---

## Opciones rápidas (por si las necesitas)

### `pdf_to_text.py`

```powershell
py pdf_to_text.py RUTA_PDF [--max-chars N] [--output-dir RUTA]
```

- `--max-chars`: tamaño máximo de cada parte (por defecto `3000`).
- `--output-dir`: base donde se crea la carpeta `nombre-libro/`.

### `text_to_audiobook.py`

```powershell
py text_to_audiobook.py BOOK [--workers N] [--prompt "texto"] [--base-dir RUTA]
```

- `BOOK`: nombre o ruta de la carpeta del libro (la que contiene `text/`).
- `--workers`: peticiones TTS en paralelo (por defecto `1`, puedes subirlo a `2` o `3`).
- `--prompt`: texto de instrucciones de estilo para la voz.
- `--base-dir`: base donde buscar la carpeta del libro si pasas solo el nombre.

DOC: 
https://docs.cloud.google.com/text-to-speech/docs/gemini-tts?hl=es-419#curl