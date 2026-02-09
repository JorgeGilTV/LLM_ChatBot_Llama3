from datadog_api_client.v1 import ApiClient, Configuration
from datadog_api_client.v1.api import DashboardsApi

def get_dashboard_widgets(api_key: str, app_key: str, dashboard_id: str):
    """
    Conecta a Datadog y extrae los widgets de un dashboard específico.
    
    Parámetros:
        api_key (str): Tu Datadog API Key
        app_key (str): Tu Datadog Application Key
        dashboard_id (str): El ID del dashboard que quieres consultar
    
    Retorna:
        list: Lista de widgets del dashboard
    """
    # Configuración de autenticación
    configuration = Configuration(
        api_key={
            "apiKeyAuth": api_key,
            "appKeyAuth": app_key,
        }
    )

    # Cliente de API
    with ApiClient(configuration) as api_client:
        dashboards_api = DashboardsApi(api_client)

        # Obtener el dashboard
        dashboard = dashboards_api.get_dashboard(dashboard_id)

        # Extraer widgets
        widgets = dashboard['widgets']
        return widgets


# Ejemplo de uso
if __name__ == "__main__":
    API_KEY = "tu_api_key"
    APP_KEY = "tu_app_key"
    DASHBOARD_ID = "abc-def-123"  # Reemplaza con el ID real de tu dashboard

    widgets = get_dashboard_widgets(API_KEY, APP_KEY, DASHBOARD_ID)
    for w in widgets:
        print(f"Widget: {w['definition']['type']} - Title: {w.get('definition', {}).get('title', 'No Title')}")
