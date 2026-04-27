"""Tests for HTTP API endpoints via FastAPI TestClient."""
import pytest
from sqlalchemy.orm import Session

from app.models.db import ApiKey, LabelTemplate, Printer


class TestAuthFlow:
    def test_login_page_loads(self, client):
        r = client.get("/login")
        assert r.status_code == 200
        assert "Zebra API" in r.text

    def test_login_bad_credentials(self, client, admin_cookie):
        r = client.post("/login", data={"username": "admin", "password": "wrong"})
        assert r.status_code == 401

    def test_login_success_redirects(self, client, admin_cookie):
        r = client.post("/login", data={"username": "admin", "password": "testpass"}, follow_redirects=False)
        assert r.status_code == 303
        assert "zebra_admin" in r.cookies


class TestPrinterEndpoints:
    def test_list_printers_requires_auth(self, client):
        r = client.get("/printers")
        assert r.status_code in (401, 403)

    def test_list_printers_with_key(self, client, sample_printer, sample_api_key):
        raw_key, _ = sample_api_key
        r = client.get("/printers", headers={"Authorization": f"Bearer {raw_key}"})
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert data[0]["ip"] == "192.168.45.208"


class TestTemplateEndpoints:
    def test_list_templates(self, client, sample_template, sample_api_key):
        raw_key, _ = sample_api_key
        r = client.get("/templates", headers={"Authorization": f"Bearer {raw_key}"})
        assert r.status_code == 200
        data = r.json()
        assert any(t["name"] == "test-label" for t in data)


class TestPrintEndpoint:
    def test_submit_print_job(self, client, sample_printer, sample_template, sample_api_key):
        raw_key, _ = sample_api_key
        r = client.post(
            "/print",
            json={
                "printer_id": sample_printer.id,
                "template_id": sample_template.id,
                "variables": {"title": "Test", "message": "Hello"},
                "quantity": 1,
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "pending"
        assert data["id"]

    def test_print_missing_printer(self, client, sample_template, sample_api_key):
        raw_key, _ = sample_api_key
        r = client.post(
            "/print",
            json={"printer_id": "fake", "template_id": sample_template.id, "variables": {}, "quantity": 1},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert r.status_code == 400

    def test_print_no_auth(self, client):
        r = client.post("/print", json={"printer_id": "x", "template_id": "y", "variables": {}, "quantity": 1})
        assert r.status_code in (401, 403)


class TestAdminPages:
    def test_dashboard_requires_login(self, client):
        r = client.get("/admin", follow_redirects=False)
        assert r.status_code == 303

    def test_dashboard_with_cookie(self, client, admin_cookie):
        r = client.get("/admin")
        assert r.status_code == 200
        assert "Dashboard" in r.text

    def test_printers_page(self, client, admin_cookie, sample_printer):
        r = client.get("/admin/printers")
        assert r.status_code == 200
        assert "192.168.45.208" in r.text

    def test_templates_page(self, client, admin_cookie):
        r = client.get("/admin/templates")
        assert r.status_code == 200

    def test_keys_page(self, client, admin_cookie):
        r = client.get("/admin/keys")
        assert r.status_code == 200

    def test_jobs_page(self, client, admin_cookie):
        r = client.get("/admin/jobs")
        assert r.status_code == 200

    def test_settings_page(self, client, admin_cookie):
        r = client.get("/admin/settings")
        assert r.status_code == 200
        assert "Job retention" in r.text
