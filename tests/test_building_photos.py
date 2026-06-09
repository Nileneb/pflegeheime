import json
import os

import pytest

from marktradar import building_photos as bp


def test_compass_maps_orbit_angle_to_camera_side():
    # Blosm-Szene: +X=Ost, +Y=Nord → Kamera bei angle 0 steht im Osten.
    assert bp.compass(0) == "O"
    assert bp.compass(90) == "N"
    assert bp.compass(180) == "W"
    assert bp.compass(270) == "S"
    assert bp.compass(45) == "NO"
    assert bp.compass(315) == "SO"


def _seed_unit(conn, traeger="Bergische Diakonie", name="Haus Otto Ohl"):
    cur = conn.execute("INSERT INTO org_units(traeger, name) VALUES (?,?)", (traeger, name))
    conn.commit()
    return cur.lastrowid


def _write_render(out_root, traeger_slug, unit_slug, n=3):
    d = os.path.join(out_root, traeger_slug, unit_slug)
    os.makedirs(d)
    rows = [{"unit": unit_slug, "view": i, "angle_deg": i * 90.0,
             "path": f"view_{i:02d}.png", "source": "blender_render",
             "created": "2026-06-09T00:00:00"} for i in range(n)]
    with open(os.path.join(d, "photos.json"), "w") as f:
        json.dump(rows, f)
    return rows


def test_ingest_is_idempotent_and_maps_units(conn, tmp_path):
    uid = _seed_unit(conn)
    out_root = str(tmp_path / "out")
    _write_render(out_root, "bergische-diakonie", "haus-otto-ohl", n=3)

    r1 = bp.ingest_renders(conn, out_root)
    assert r1["inserted"] == 3 and r1["units"] == 1 and r1["unmatched"] == []

    photos = bp.photos_for_unit(conn, uid)
    assert len(photos) == 3
    assert photos[0]["style"] == "blender_render"
    assert {p["perspective"] for p in photos} == {"O", "N", "W"}  # angles 0/90/180

    r2 = bp.ingest_renders(conn, out_root)  # zweiter Lauf fügt nichts hinzu
    assert r2["inserted"] == 0


def test_ingest_reports_unmatched_folder(conn, tmp_path):
    out_root = str(tmp_path / "out")
    _write_render(out_root, "fremder-traeger", "unbekanntes-haus", n=1)
    r = bp.ingest_renders(conn, out_root)
    assert r["inserted"] == 0
    assert "fremder-traeger/unbekanntes-haus" in r["unmatched"]


def test_update_photo_sets_fields_and_rejects_bad_style(conn, tmp_path):
    uid = _seed_unit(conn)
    out_root = str(tmp_path / "out")
    _write_render(out_root, "bergische-diakonie", "haus-otto-ohl", n=1)
    bp.ingest_renders(conn, out_root)
    pid = bp.photos_for_unit(conn, uid)[0]["id"]

    bp.update_photo(conn, pid, chosen=True, style="photo", perspective="S")
    p = bp.photos_for_unit(conn, uid)[0]
    assert p["chosen"] == 1 and p["style"] == "photo" and p["perspective"] == "S"

    with pytest.raises(ValueError):
        bp.update_photo(conn, pid, style="hologram")


def test_set_order_writes_rank(conn, tmp_path):
    uid = _seed_unit(conn)
    out_root = str(tmp_path / "out")
    _write_render(out_root, "bergische-diakonie", "haus-otto-ohl", n=3)
    bp.ingest_renders(conn, out_root)
    ids = [p["id"] for p in bp.photos_for_unit(conn, uid)]

    bp.set_order(conn, uid, list(reversed(ids)))
    reordered = [p["id"] for p in bp.photos_for_unit(conn, uid)]
    assert reordered == list(reversed(ids))


def test_add_manual_appends_with_next_rank(conn, tmp_path):
    uid = _seed_unit(conn)
    out_root = str(tmp_path / "out")
    _write_render(out_root, "bergische-diakonie", "haus-otto-ohl", n=2)
    bp.ingest_renders(conn, out_root)

    res = bp.add_manual(conn, uid, "bergische-diakonie/haus-otto-ohl/manual/front.jpg",
                        style="photo", perspective="S")
    assert res["inserted"] == 1
    photos = bp.photos_for_unit(conn, uid)
    assert len(photos) == 3
    assert photos[-1]["rank"] == 2 and photos[-1]["style"] == "photo"


def test_units_with_photos_counts_chosen(conn, tmp_path):
    uid = _seed_unit(conn)
    out_root = str(tmp_path / "out")
    _write_render(out_root, "bergische-diakonie", "haus-otto-ohl", n=3)
    bp.ingest_renders(conn, out_root)
    pid = bp.photos_for_unit(conn, uid)[0]["id"]
    bp.update_photo(conn, pid, chosen=True)

    rows = bp.units_with_photos(conn)
    assert len(rows) == 1
    assert rows[0]["n"] == 3 and rows[0]["n_chosen"] == 1
