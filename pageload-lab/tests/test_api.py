from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_ingress_ui_and_api(tmp_path, monkeypatch):
    monkeypatch.setenv('PAGELAB_DATA_DIR',str(tmp_path));monkeypatch.setenv('PAGELAB_TESTING','1')
    with patch('app.main.BrowserEngine.start',new=AsyncMock()), patch('app.main.BrowserEngine.stop',new=AsyncMock()):
        from app.main import app
        with TestClient(app) as client:
            response=client.get('/',headers={'x-ingress-path':'/api/hassio_ingress/token'})
            assert response.status_code==200
            assert '/api/hassio_ingress/token/static/app.css' in response.text
            assert client.put('/api/settings',json={'authorization_confirmed':True,'onboarding_complete':True}).status_code==200
            payload={'name':'API Test','url':'https://93.184.216.34/','mode':'http','start_type':'immediate','timezone':'Europe/Zurich','frequency_per_minute':2,'frequency_mode':'random','stay_mode':'none','stay_min_seconds':0,'stay_max_seconds':0,'duration_mode':'requests','duration_days':0,'duration_hours':0,'duration_minutes':0,'max_requests':5,'max_concurrency':2,'proxy_mode':'direct','reporting_enabled':True,'authorization_confirmed':True}
            created=client.post('/api/tests',json=payload)
            assert created.status_code==201,created.text
            test_id=created.json()['test']['id']
            assert client.get(f'/api/tests/{test_id}').status_code==200
            duplicate=client.post(f'/api/tests/{test_id}/duplicate')
            assert duplicate.status_code==201 and duplicate.json()['total_requests']==0
            reports=client.get('/api/reports',params={'name':'API','url':'93.184','date_from':'2020-01-01','date_to':'2099-12-31'})
            assert reports.status_code==200 and any(row['id']==test_id for row in reports.json())
            assert client.delete(f'/api/tests/{test_id}').status_code==204


def test_validation_returns_understandable_error(tmp_path, monkeypatch):
    monkeypatch.setenv('PAGELAB_DATA_DIR',str(tmp_path));monkeypatch.setenv('PAGELAB_TESTING','1')
    with patch('app.main.BrowserEngine.start',new=AsyncMock()), patch('app.main.BrowserEngine.stop',new=AsyncMock()):
        from app.main import app
        with TestClient(app) as client:
            response=client.post('/api/tests',json={'name':'x'})
            assert response.status_code==422 and response.json().get('error')


def test_production_access_is_ingress_only_and_csrf_protected(tmp_path, monkeypatch):
    monkeypatch.setenv('PAGELAB_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('PAGELAB_TESTING', raising=False)
    with patch('app.main.BrowserEngine.start', new=AsyncMock()), patch('app.main.BrowserEngine.stop', new=AsyncMock()):
        from app.main import app
        with TestClient(app, client=('192.0.2.20', 50000)) as remote:
            assert remote.get('/').status_code == 403
        with TestClient(app, client=('127.0.0.1', 50000)) as local:
            assert local.get('/health').status_code in {200, 503}
            response = local.put('/api/settings', json={'onboarding_complete': True})
            assert response.status_code == 403
            assert 'Sicherheitsprüfung' in response.json()['error']
