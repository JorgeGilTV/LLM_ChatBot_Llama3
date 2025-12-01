
document.addEventListener("DOMContentLoaded", () => {
  const sendBtn = document.getElementById("sendBtn");
  const inputField = document.getElementById("input");
  const resultDiv = document.getElementById("result");

  sendBtn.addEventListener("click", async () => {
    const query = inputField.value.trim();
    const selectedTools = Array.from(document.querySelectorAll("input[type=checkbox]:checked"))
                               .map(cb => cb.value);

    if (!query) {
      alert("Por favor ingresa un texto.");
      return;
    }
    if (selectedTools.length === 0) {
      alert("Selecciona al menos una herramienta.");
      return;
    }

    // Mostrar mensaje de carga
    resultDiv.innerHTML = `<p>⏳ Procesando...</p>`;

    try {
      const formData = new URLSearchParams();
      formData.append("input", query);
      selectedTools.forEach(tool => formData.append("tool", tool));

      const response = await fetch("http://127.0.0.1:5000/", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: formData.toString()
      });

      if (!response.ok) {
        throw new Error(`Error ${response.status}: ${response.statusText}`);
      }

      const html = await response.text();
      resultDiv.innerHTML = html;
    } catch (error) {
      resultDiv.innerHTML = `<p style="color:red;">❌ Error: ${error.message}</p>`;
    }
  });
});
