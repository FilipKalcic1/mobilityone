import pytest
from fastapi.testclient import TestClient
from main import app
from unittest.mock import patch, AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_lifespan_success():
    # Mock Redis mora imati async close metodu
    mock_redis = MagicMock()
    mock_redis.close = AsyncMock()
    
    mock_gateway_cls = MagicMock()
    mock_gateway_instance = mock_gateway_cls.return_value
    mock_gateway_instance.close = AsyncMock()

    with patch("main.redis.from_url", return_value=mock_redis), \
         patch("main.FastAPILimiter.init", new=AsyncMock()), \
         patch("main.ToolRegistry") as MockRegistry, \
         patch("main.OpenAPIGateway", new=mock_gateway_cls):
        
        mock_reg = MockRegistry.return_value
        mock_reg.load_swagger = AsyncMock()
        
        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200

@pytest.mark.asyncio
async def test_lifespan_swagger_missing():
    mock_redis = MagicMock()
    mock_redis.close = AsyncMock()
    
    mock_gateway_cls = MagicMock()
    mock_gateway_instance = mock_gateway_cls.return_value
    mock_gateway_instance.close = AsyncMock()

    with patch("main.redis.from_url", return_value=mock_redis), \
         patch("main.FastAPILimiter.init", new=AsyncMock()), \
         patch("main.ToolRegistry") as MockRegistry, \
         patch("main.OpenAPIGateway", new=mock_gateway_cls):
        
        mock_reg = MockRegistry.return_value
        # Simuliramo grešku - main.py je mora uhvatiti!
        mock_reg.load_swagger = AsyncMock(side_effect=FileNotFoundError("Missing"))
        
        with TestClient(app) as client:
            # Mora vratiti 200 jer smo uhvatili grešku u main.py
            response = client.get("/health")
            assert response.status_code == 200

@pytest.mark.asyncio
async def test_missing_api_url_config():
    mock_redis = MagicMock()
    mock_redis.close = AsyncMock() # [FIX] Rješava TypeError

    with patch("main.redis.from_url", return_value=mock_redis), \
         patch("main.FastAPILimiter.init", new=AsyncMock()), \
         patch("main.ToolRegistry"), \
         patch("main.settings") as mock_settings:
        
        mock_settings.REDIS_URL = "redis://test"
        mock_settings.MOBILITY_API_URL = None 
        
        with pytest.raises(ValueError):
            with TestClient(app):
                pass