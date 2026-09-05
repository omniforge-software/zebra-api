"""Tests for HTTP API endpoints via FastAPI TestClient."""
import pytest
from sqlalchemy.orm import Session

from app.models.db import ApiKey, LabelTemplate, PrintJob, Printer
from app.services.jobs import DIRECT_ZPL_TEMPLATE_ID


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


class TestDirectPrintEndpoint:
    @pytest.fixture(autouse=True)
    def skip_background_print(self, monkeypatch):
        async def skip_print(_job_id):
            return None
        monkeypatch.setattr("app.routers.jobs.process_print_job", skip_print)

    def test_submit_poll_and_admin_history(self, client, db_session, sample_printer, sample_api_key, admin_cookie):
        raw_key, api_key = sample_api_key
        headers = {"Authorization": f"Bearer {raw_key}"}
        response = client.post("/print/zpl", headers=headers, json={
            "printer_id": sample_printer.id, "zpl": "^XA^FDHello {{literal}}^FS^XZ",
        })
        assert response.status_code == 202
        data = response.json()
        assert data["template_id"] == DIRECT_ZPL_TEMPLATE_ID
        assert data["quantity"] == 1
        assert data["status"] == "pending"
        assert client.get(f"/jobs/{data['id']}", headers=headers).json() == data
        assert db_session.get(PrintJob, data["id"]).api_key_id == api_key.id
        for path in ("/admin", "/admin/jobs"):
            page = client.get(path)
            assert page.status_code == 200
            assert "Direct ZPL (system)" in page.text

        for path, form in (
            (f"/admin/templates/{DIRECT_ZPL_TEMPLATE_ID}", {"name": "changed", "zpl_body": "^XA^XZ"}),
            (f"/admin/templates/{DIRECT_ZPL_TEMPLATE_ID}/delete", {}),
            (f"/admin/templates/{DIRECT_ZPL_TEMPLATE_ID}/test-print", {"printer_id": sample_printer.id}),
        ):
            assert client.post(path, data=form).status_code == 400
        db_session.expire_all()
        assert db_session.get(LabelTemplate, DIRECT_ZPL_TEMPLATE_ID).zpl_body == "{{ zpl }}"

    def test_requires_auth(self, client, db_session):
        response = client.post("/print/zpl", json={"printer_id": "fake", "zpl": "^XA^XZ"})
        assert response.status_code == 401
        assert db_session.query(PrintJob).count() == 0

    @pytest.mark.parametrize("changes,status", [
        ({"printer_id": "missing"}, 400),
        ({"zpl": "invalid"}, 400),
        ({"zpl": ""}, 422),
        ({"zpl": None}, 422),
        ({"quantity": 0}, 422),
        ({"quantity": 101}, 400),
    ])
    def test_rejects_invalid_requests(self, client, db_session, sample_printer, sample_api_key, changes, status):
        raw_key, _ = sample_api_key
        body = {"printer_id": sample_printer.id, "zpl": "^XA^XZ", **changes}
        response = client.post("/print/zpl", headers={"Authorization": f"Bearer {raw_key}"}, json=body)
        assert response.status_code == status
        assert db_session.query(PrintJob).count() == 0


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

    def test_admin_test_print_schedules_job(self, client, admin_cookie, sample_printer, sample_template, monkeypatch):
        async def skip_print(_job_id):
            return None

        monkeypatch.setattr("app.routers.admin.process_print_job", skip_print)
        r = client.post(
            f"/admin/templates/{sample_template.id}/test-print",
            data={"printer_id": sample_printer.id, "quantity": 1},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/jobs"

    def test_settings_page(self, client, admin_cookie):
        r = client.get("/admin/settings")
        assert r.status_code == 200
        assert "Job retention" in r.text
