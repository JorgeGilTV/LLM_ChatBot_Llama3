from mcp.server.fastmcp import FastMCP
from main import TOOLS

mcp = FastMCP("Servidor MCP de Jorge")

# Registrar herramientas
for name, tool_def in TOOLS.items():
    def make_tool(func, name=name, description=tool_def["description"]):
        @mcp.tool(name=name, description=description)
        def tool_wrapper(input: str) -> str:
            return func(input)
        return tool_wrapper

    make_tool(tool_def["function"])

# Ejecutar servidor SSE con configuración por defecto
if __name__ == "__main__":
    print("🧠 MCP Server corriendo en http://127.0.0.1:8000/sse/")
    mcp.run(transport="sse")