import math
import sqlite3

import pytest

from marktradar.blender_geo import _bbox, _facility_from_db


def test_bbox_centers_on_point_and_scales_with_radius():
    lat, lon, r = 51.2806, 7.0386, 320.0
    min_lat, max_lat, min_lon, max_lon = _bbox(lat, lon, r)
    assert math.isclose((min_lat + max_lat) / 2, lat, abs_tol=1e-9)
    assert math.isclose((min_lon + max_lon) / 2, lon, abs_tol=1e-9)
    assert math.isclose(max_lat - lat, r / 111320.0, rel_tol=1e-6)
    assert (max_lon - lon) > (max_lat - lat)


def _db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE org_units (id INTEGER PRIMARY KEY, name TEXT, traeger TEXT, "
              "lat REAL, lon REAL)")
    c.execute("INSERT INTO org_units (id,name,traeger,lat,lon) VALUES "
              "(1,'Haus Wülfrath','Bergische Diakonie',51.2806,7.0386)")
    c.execute("INSERT INTO org_units (id,name,traeger,lat,lon) VALUES "
              "(2,'Haus Ohne Geo','Bergische Diakonie',NULL,NULL)")
    return c


def test_facility_from_db_by_id_and_name():
    c = _db()
    assert _facility_from_db(c, 1) == (51.2806, 7.0386, "Haus Wülfrath", "Bergische Diakonie")
    lat, lon, name, traeger = _facility_from_db(c, "Wülfrath")
    assert name == "Haus Wülfrath" and lat == 51.2806


def test_facility_from_db_raises_without_geo():
    c = _db()
    with pytest.raises(ValueError, match="nicht geokodiert"):
        _facility_from_db(c, 2)


def test_facility_from_db_raises_when_missing():
    c = _db()
    with pytest.raises(ValueError, match="nicht gefunden"):
        _facility_from_db(c, 999)
