document.addEventListener("DOMContentLoaded", () => {
  const tools = {
    Read_Itrack: "Read open tickets from Itrack",
    Read_Wiki: "Read documents from AT&T Wiki",
    How_to_fix: "Generate recommendations using LLaMA, Wiki, Itrack and prompt provided",
    MCP_Connect: "Check if the MCP server is active",
    Clean_Input: "Clean the input"
  };

  const container = document.getElementById("tool-buttons");
  const inputField = document.getElementById("input");
  const resultBox = document.getElementById("result");

  Object.entries(tools).forEach(([name, desc]) => {
    const btn = document.createElement("button");
    btn.className = "tool-btn";
    btn.textContent = name;
    btn.title = desc;

    btn.addEventListener("click", async () => {
      if (name === "Clean_Input") {
        inputField.value = "";
        resultBox.textContent = "✂️ Input erased.";
        return;
      }

      const input = inputField.value.trim();
      resultBox.textContent = `⏳ Running "${name}"...`;

      try {
        const response = await fetch("http://127.0.0.1:5000/tool", {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded"
          },
          body: `tool=${encodeURIComponent(name)}&input=${encodeURIComponent(input)}`
        });

        const html = await response.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, "text/html");

        const resultHeader = Array.from(doc.querySelectorAll("h2")).find(h => h.textContent.includes("Response:"));
        const resultDiv = resultHeader?.nextElementSibling;
        resultBox.innerHTML = resultDiv?.innerHTML || html; // fallback to full HTML if no header found

      } catch (error) {
        resultBox.textContent = `❌ Error: ${error.message}`;
      }
    });

    container.appendChild(btn);
  });
});
