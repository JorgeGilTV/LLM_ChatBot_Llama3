from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import time
import os
from dotenv import load_dotenv

load_dotenv()

# Get Slack token from environment variable
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN")
if not SLACK_TOKEN:
    raise ValueError("SLACK_BOT_TOKEN not found in environment variables. Please set it in your .env file.")

client = WebClient(token=SLACK_TOKEN)
arlochat_user_id = "U0A3U7ZS3FW"  # ID del bot arlochat

def ask_arlo(question, wait_seconds=5):
    """
    Envía una pregunta a arlochat en Slack y devuelve su respuesta.
    - question: texto de la pregunta
    - wait_seconds: tiempo de espera antes de leer la respuesta
    """
    try:
        # 1. Abrir conversación directa con arlochat
        dm = client.conversations_open(users=[arlochat_user_id])
        channel_id = dm["channel"]["id"]

        # 2. Enviar mensaje
        response = client.chat_postMessage(channel=channel_id, text=question)
        ts = response["ts"]

        # 3. Esperar unos segundos para que arlochat responda
        time.sleep(wait_seconds)

        # 4. Leer historial de mensajes en ese DM
        history = client.conversations_replies(channel=channel_id, ts=ts)

        # 5. Buscar la respuesta de arlochat
        for msg in history["messages"]:
            if msg.get("user") == arlochat_user_id:
                return msg.get("text")

        return "No se encontró respuesta de arlochat."

    except SlackApiError as e:
        return f"Error: {e.response['error']}"

# Ejemplo de uso
reply = ask_arlo("Hola arlochat, ¿puedes ayudarme con X?")
print("Respuesta de arlochat:", reply)
