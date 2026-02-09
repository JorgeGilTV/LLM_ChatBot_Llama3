import os, requests

def ask_gemini(prompt: str, selected_tools: list) -> str:
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "Error: GEMINI_API_KEY is not defined in env file."
        if "Ask_Gemini" in selected_tools:
            prompt = f"Execute the following prompt, i need legible like html formated but preserving the current style.:\n{prompt}"
        model = "models/gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            return f"Error {response.status_code}: {response.text}"
        data = response.json()
        output = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return output if output else "Gemini is not working properly."
    except Exception as e:
        return f"Error executing Gemini: {e}"
