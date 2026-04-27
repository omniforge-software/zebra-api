"""Tests for ZPL rendering, variable substitution, and ^PQ quantity injection."""
import pytest

from app.services.zpl_render import extract_variables, inject_quantity, render_zpl, validate_zpl


# ---------------------------------------------------------------------------
# extract_variables
# ---------------------------------------------------------------------------

class TestExtractVariables:
    def test_simple(self):
        assert extract_variables("^FD{{ title }}^FS") == ["title"]

    def test_multiple_sorted(self):
        assert extract_variables("{{ b }} {{ a }}") == ["a", "b"]

    def test_no_duplicates(self):
        assert extract_variables("{{ x }} {{ x }}") == ["x"]

    def test_no_vars(self):
        assert extract_variables("^XA^XZ") == []

    def test_tight_braces(self):
        assert extract_variables("{{name}}") == ["name"]

    def test_underscored(self):
        assert extract_variables("{{ line_1 }}") == ["line_1"]


# ---------------------------------------------------------------------------
# validate_zpl
# ---------------------------------------------------------------------------

class TestValidateZpl:
    def test_valid(self):
        validate_zpl("^XA\n^FDhello^FS\n^XZ")

    def test_missing_xxa(self):
        with pytest.raises(ValueError, match="start with \\^XA"):
            validate_zpl("^FDhello^FS\n^XZ")

    def test_missing_xxz(self):
        with pytest.raises(ValueError, match="end with \\^XZ"):
            validate_zpl("^XA\n^FDhello^FS")


# ---------------------------------------------------------------------------
# inject_quantity
# ---------------------------------------------------------------------------

class TestInjectQuantity:
    def test_injects_before_xz(self):
        result = inject_quantity("^XA\n^FDtest^FS\n^XZ", 3)
        assert "^PQ3" in result
        assert result.strip().endswith("^XZ")

    def test_replaces_existing_pq(self):
        result = inject_quantity("^XA\n^PQ1\n^XZ", 5)
        assert "^PQ5" in result
        assert "^PQ1" not in result

    def test_quantity_zero_raises(self):
        with pytest.raises(ValueError, match="at least 1"):
            inject_quantity("^XA\n^XZ", 0)

    def test_quantity_over_max_raises(self):
        with pytest.raises(ValueError, match="cannot exceed"):
            inject_quantity("^XA\n^XZ", 999)

    def test_no_xz_raises(self):
        with pytest.raises(ValueError, match="end with \\^XZ"):
            inject_quantity("^XA\n^FDhello^FS", 1)


# ---------------------------------------------------------------------------
# render_zpl (integration of substitution + validation + injection)
# ---------------------------------------------------------------------------

class TestRenderZpl:
    def test_basic_render(self):
        template = "^XA\n^FD{{ title }}^FS\n^XZ"
        result = render_zpl(template, ["title"], {"title": "Hello"}, 1)
        assert b"^FDHello^FS" in result
        assert b"^PQ1" in result

    def test_missing_variable_raises(self):
        with pytest.raises(ValueError, match="Missing template variables"):
            render_zpl("^XA\n^FD{{ title }}^FS\n^XZ", ["title"], {}, 1)

    def test_extra_variable_raises(self):
        with pytest.raises(ValueError, match="Unknown template variables"):
            render_zpl("^XA\n^FDhello^FS\n^XZ", [], {"rogue": "val"}, 1)

    def test_multi_variable(self):
        template = "^XA\n^FD{{ a }}^FS\n^FD{{ b }}^FS\n^XZ"
        result = render_zpl(template, ["a", "b"], {"a": "A", "b": "B"}, 2)
        assert b"^FDA^FS" in result
        assert b"^FDB^FS" in result
        assert b"^PQ2" in result

    def test_returns_bytes(self):
        result = render_zpl("^XA\n^FDtest^FS\n^XZ", [], {}, 1)
        assert isinstance(result, bytes)
