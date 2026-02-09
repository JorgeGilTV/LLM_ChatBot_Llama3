from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import time
import os
import html
from dotenv import load_dotenv

load_dotenv()

# ID del bot arlochat
ARLOCHAT_USER_ID = "U0A3U7ZS3FW"

def ask_arlo(question: str = "", wait_seconds: int = 5) -> str:
    """
    Envía una pregunta a arlochat en Slack y devuelve su respuesta.
    Args:
        question: texto de la pregunta
        wait_seconds: tiempo de espera antes de leer la respuesta (default: 5)
    Returns:
        HTML formatted response
    """
    print("=" * 80)
    print("💬 Asking ArloChat via Slack")
    print(f"📝 Question: '{question}'")
    
    # Check if Slack token is configured
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        return """
        <div style='background-color: #fff3cd; padding: 12px; border-left: 4px solid #ffc107; border-radius: 4px; margin: 8px 0;'>
            <p style='margin: 0; color: #856404;'>
                ⚠️ <strong>Slack Bot Token not configured.</strong><br>
                Please set <code>SLACK_BOT_TOKEN</code> in your .env file to use the Ask_ARLOCHAT feature.
            </p>
        </div>
        """
    
    if not question or not question.strip():
        return """
        <div style='background-color: #fff3cd; padding: 12px; border-left: 4px solid #ffc107; border-radius: 4px; margin: 8px 0;'>
            <p style='margin: 0; color: #856404;'>
                ⚠️ <strong>No question provided.</strong><br>
                Please enter a question to ask ArloChat.
            </p>
        </div>
        """
    
    try:
        client = WebClient(token=slack_token)
        
        # 1. Abrir conversación directa con arlochat
        dm = client.conversations_open(users=[ARLOCHAT_USER_ID])
        channel_id = dm["channel"]["id"]

        # 2. Enviar mensaje
        response = client.chat_postMessage(channel=channel_id, text=question)
        ts = response["ts"]

        # 3. Esperar unos segundos para que arlochat responda
        time.sleep(wait_seconds)

        # 4. Leer historial de mensajes en ese DM
        history = client.conversations_replies(channel=channel_id, ts=ts)

        # 5. Buscar la respuesta de arlochat
        arlo_response = None
        for msg in history["messages"]:
            if msg.get("user") == ARLOCHAT_USER_ID:
                arlo_response = msg.get("text")
                break

        if not arlo_response:
            arlo_response = "No se encontró respuesta de arlochat. Intenta aumentar el tiempo de espera."

        # Format response as HTML
        output = f"""
        <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                    padding: 12px; 
                    border-radius: 6px; 
                    margin: 8px 0;
                    color: white;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);'>
            <h2 style='margin: 0 0 6px 0; color: white; font-size: 16px; font-weight: bold;'>💬 ArloChat Response</h2>
            <p style='margin: 0; font-size: 12px; opacity: 0.95;'>
                Question: {html.escape(question)}
            </p>
        </div>
        <div style='background-color: #ffffff; padding: 12px; border-radius: 4px; margin: 8px 0; box-shadow: 0 1px 2px rgba(0,0,0,0.06);'>
            <div style='white-space: pre-wrap; color: #2d3748; font-size: 13px; line-height: 1.6;'>
                {html.escape(arlo_response)}
            </div>
        </div>
        """
        
        print(f"✅ Response received from ArloChat")
        return output

    except SlackApiError as e:
        error_msg = e.response.get('error', 'Unknown error')
        print(f"❌ Slack API Error: {error_msg}")
        return f"""
        <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px; margin: 8px 0;'>
            <p style='margin: 0; color: #c53030;'>
                ❌ <strong>Slack API Error:</strong> {html.escape(error_msg)}
            </p>
        </div>
        """
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"""
        <div style='background-color: #fee; padding: 12px; border-left: 4px solid #f56565; border-radius: 4px; margin: 8px 0;'>
            <p style='margin: 0; color: #c53030;'>
                ❌ <strong>Error:</strong> {html.escape(str(e))}
            </p>
        </div>
        """
