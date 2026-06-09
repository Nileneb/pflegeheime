import json

from marktradar.blender_render import _slug, write_manifest


def test_slug_transliterates_and_lowercases():
    assert _slug("Haus Wülfrath / Süd") == "haus-wulfrath-sud"
    assert _slug("  A..B  ") == "a-b"


def test_write_manifest_roundtrip(tmp_path):
    rows = [{"unit": "haus-x", "view": 0, "angle_deg": 0.0,
             "path": "view_00.png", "source": "blender_render", "created": "2026-06-09T00:00:00"}]
    p = write_manifest(str(tmp_path), rows)
    assert p.endswith("photos.json")
    data = json.loads(open(p).read())
    assert data[0]["path"] == "view_00.png" and data[0]["source"] == "blender_render"
