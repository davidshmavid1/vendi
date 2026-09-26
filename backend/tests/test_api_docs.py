def test_openapi_schema_lists_health_endpoints(client):
    # Docs are enabled in test settings (as in development).
    response = client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/health/live" in paths
    assert "/api/v1/health/ready" in paths
