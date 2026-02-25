import os
import re
import pdfplumber
from dotenv import load_dotenv
from google.cloud import texttospeech

# ==============================
# CONFIG
# ==============================

PDF_PATH = "el-muro.pdf"
OUTPUT_FOLDER = "audio_output"
MAX_CHARS = 3000 
VOICE_NAME = "Enceladus"
MODEL_NAME = "gemini-2.5-pro-tts"
LANGUAGE_CODE = "es-ES"

# ==============================
# SETUP
# ==============================

load_dotenv()

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")

os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# ==============================
# 1️⃣ EXTRAER TEXTO
# ==============================

def extract_text_from_pdf(path):
    full_text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
    return full_text

# ==============================
# 2️⃣ LIMPIEZA
# ==============================

def clean_text(text):
    text = re.sub(r'\n+', '\n', text)
    text = re.sub(r' +', ' ', text)
    return text.strip()

# ==============================
# 3️⃣ DIVIDIR EN CHUNKS
# ==============================

def chunk_text(text, max_chars=3000):
    sentences = re.split(r'(?<=[.!?]) +', text)
    chunks = []
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

# ==============================
# 4️⃣ GENERAR AUDIO CON GEMINI TTS (Cloud Text-to-Speech)
# ==============================

def synthesize(prompt: str, text: str, output_filepath: str = "output.mp3"):
    """Sintetiza voz desde el texto y la guarda en un MP3."""
    client = texttospeech.TextToSpeechClient()

    synthesis_input = texttospeech.SynthesisInput(text=text, prompt=prompt)

    # Voz y modelo TTS basados en tu ejemplo
    voice = texttospeech.VoiceSelectionParams(
        language_code=LANGUAGE_CODE,
        name=VOICE_NAME,
        model_name=MODEL_NAME,
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )

    response = client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )

    with open(output_filepath, "wb") as out:
        out.write(response.audio_content)
        print(f"Audio content written to file: {output_filepath}")

# ==============================
# MAIN
# ==============================

def main():
    print("Extrayendo texto del PDF...")
    text = extract_text_from_pdf(PDF_PATH)

    print("Limpiando texto...")
    text = clean_text(text)

    print("Dividiendo en partes...")
    chunks = chunk_text(text)

    print(f"Total de partes: {len(chunks)}")

    # ⚠️ PRUEBA: solo primer chunk
    test_chunks = chunks[:1]

    return

    for i, chunk in enumerate(test_chunks):
        print(f"Generando audio {i+1}/{len(test_chunks)}...")

        file_name = os.path.join(
            OUTPUT_FOLDER,
            f"chunk_{str(i+1).zfill(3)}.mp3"
        )

        synthesize(
            prompt="Read the following text naturally.",
            text=chunk,
            output_filepath=file_name,
        )

    print("✅ Audio generado correctamente")

if __name__ == "__main__":
    main()