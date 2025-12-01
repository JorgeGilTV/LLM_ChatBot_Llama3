import os, json

HISTORY_FILE = 'static/search_history.json'

def add_to_history(query, result):
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            history = json.load(f)
    except:
        history = []
    history.append({'query': query, 'result': result})
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history[-10:], f)

def get_history():
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            history = json.load(f)
            return history[::-1]  # últimos 10, más recientes primero
    except:
        return []

if not os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)
