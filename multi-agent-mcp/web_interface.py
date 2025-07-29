from flask import Flask, request, render_template
from main import ask_claude, ask_llama, search_web, crawl_url, search_code

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    response = ""
    if request.method == "POST":
        tool = request.form["tool"]
        prompt = request.form["prompt"]

        if tool == "claude":
            response = ask_claude(prompt)
        elif tool == "llama":
            response = ask_llama(prompt)
        elif tool == "search":
            response = search_web(prompt)
        elif tool == "scrape":
            response = crawl_url(prompt)
        elif tool == "code":
            response = search_code(prompt)
        else:
            response = "❌ Herramienta no reconocida."

    return render_template("index.html", response=response)

if __name__ == "__main__":
    app.run(debug=True)