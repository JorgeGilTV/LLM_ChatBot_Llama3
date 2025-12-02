import os
import requests

def ask_copilot(prompt: str, selected_tools: list = None) -> str:
    """
    Ejecuta una consulta contra Azure OpenAI y devuelve la respuesta en HTML.
    - prompt: texto de entrada
    - selected_tools: lista de herramientas seleccionadas (ej. ["Ask_OpenAI"])
    """
    try:
        api_key = os.getenv("COPILOT_API_KEY")
        if not api_key:
            return "<p><strong>Error:</strong> COPILOT_API_KEY no está definido en variables de entorno.</p>"

        # Ajustar prompt si la tool es Ask_OpenAI
        if selected_tools and "Ask_OpenAI" in selected_tools:
            prompt = (
                "Execute the following prompt. "
                "I need legible HTML formatted output but preserving the current style:\n"
                f"{prompt}"
            )

        # Datos del deployment (ajusta según tu configuración)
        deployment_name = "AIHackathon1-gpt-5-mini"
        endpoint = "https://att-genai-openai.openai.azure.com"
        api_version = "2024-12-01-preview"

        url = f"{endpoint}/openai/deployments/{deployment_name}/chat/completions?api-version={api_version}"
        headers = {
            "Content-Type": "application/json",
            "api-key": api_key
        }

        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "top_p": 1,
            "frequency_penalty": 0,
            "presence_penalty": 0,
            "max_tokens": 2048
        }

        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            return f"<p><strong>Error {response.status_code}:</strong> {response.text}</p>"

        data = response.json()
        output = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return output if output else "<p><strong>Error:</strong> OpenAI no devolvió texto.</p>"

    except Exception as e:
        return f"<p><strong>Error ejecutando OpenAI:</strong> {e}</p>"