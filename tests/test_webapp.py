"""Web UI 接口冒烟测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.config import load_config
from chanlun_trader.webapp import app


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def test_config_api(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    assert "backtest" in data


def test_kline_api(client):
    cfg = load_config()
    if not Path(cfg["tdx"]["vipdoc"]).exists():
        pytest.skip("本机没有通达信数据，跳过")
    r = client.get("/api/kline/600000?market=1&bars=80")
    assert r.status_code == 200
    data = r.json()
    assert len(data["kline"]) <= 80
    assert "bi" in data


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert '<div id="app"></div>' in r.text
    assert 'id="app"' in r.text


@pytest.mark.parametrize("path", ["/research", "/research/ai-researcher", "/research/operations", "/research/closeout", "/research/governance"])
def test_research_history_routes_serve_vue_shell(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert '<div id="app"></div>' in r.text
    assert "/assets/" in r.text
