"""Kuratier-Datenschicht für die Gebäude-Fotos (Blender-Renders + manuell ergänzte).

Liest die von `blender_render` erzeugten `out/buildings/<traeger>/<unit>/photos.json`
in die `building_photos`-Tabelle (idempotent) und stellt CRUD für das lokale
Kuratier-Tool (`curate.py`) bereit: Perspektive, Style/Typ, Reihenfolge, „gut"-Flag.
Reine Helfer ohne HTTP — über `tests/test_building_photos.py` ohne Server testbar."""
import json
import os
from datetime import datetime, timezone

from marktradar.blender_render import _slug

# Style = Herkunft/Typ des Bildes (User-Entscheid: Quelle-Tag, nicht freier Text).
STYLES = ["blender_render", "photo", "aerial", "drawing"]
# 8 Himmelsrichtungen der Kameraposition; die 4 Kardinalen sind eine Teilmenge davon.
PERSPECTIVES = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]


def compass(angle_deg):
    """Orbit-Winkel (CCW von +X) → Himmelsrichtung der Kameraposition. WHY(geo): die
    Blosm-Szene hat +X=Ost, +Y=Nord, also steht die Kamera bei angle 0 im Osten (zeigt die
    Ostfassade), bei 90 im Norden. bearing = (90 - angle) im Uhrzeigersinn ab Nord."""
    bearing = (90.0 - float(angle_deg)) % 360.0
    return PERSPECTIVES[round(bearing / 45.0) % 8]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unit_slug_map(conn):
    """(traeger_slug, unit_slug) → org_units.id, um Render-Ordner auf Einheiten zu mappen."""
    out = {}
    for r in conn.execute("SELECT id, name, traeger FROM org_units"):
        out[(_slug(r["traeger"] or ""), _slug(r["name"]))] = r["id"]
    return out


def ingest_renders(conn, out_root="out/buildings"):
    """Scannt out_root nach photos.json und spielt die Blender-Renders in building_photos.
    Idempotent (UNIQUE(unit_id,path) + INSERT OR IGNORE). Render-Ordner ohne passende
    Einheit werden NICHT verschluckt, sondern in `unmatched` zurückgemeldet."""
    smap = _unit_slug_map(conn)
    inserted, units, unmatched = 0, set(), []
    if not os.path.isdir(out_root):
        return {"inserted": 0, "units": 0, "unmatched": [], "out_root": out_root}
    for traeger_slug in sorted(os.listdir(out_root)):
        tdir = os.path.join(out_root, traeger_slug)
        if not os.path.isdir(tdir):
            continue
        for unit_slug in sorted(os.listdir(tdir)):
            mpath = os.path.join(tdir, unit_slug, "photos.json")
            if not os.path.isfile(mpath):
                continue
            uid = smap.get((traeger_slug, unit_slug))
            if uid is None:  # WHY: Slug-Mismatch sichtbar machen, nicht still droppen
                unmatched.append(f"{traeger_slug}/{unit_slug}")
                continue
            with open(mpath) as f:
                rows = json.load(f)
            for row in rows:
                rel = f"{traeger_slug}/{unit_slug}/{row['path']}"
                cur = conn.execute(
                    "INSERT OR IGNORE INTO building_photos"
                    "(unit_id, path, perspective, style, rank, chosen, created)"
                    " VALUES (?,?,?,?,?,0,?)",
                    (uid, rel, compass(row.get("angle_deg", 0)), "blender_render",
                     int(row.get("view", 0)), _now()))
                inserted += cur.rowcount
                units.add(uid)
    conn.commit()
    return {"inserted": inserted, "units": len(units), "unmatched": unmatched,
            "out_root": out_root}


def units_with_photos(conn):
    """Einheiten mit Fotos für die Tool-Sidebar: id, name, traeger, Anzahl, davon gewählt."""
    return [dict(r) for r in conn.execute(
        "SELECT u.id, u.name, u.traeger, count(p.id) n,"
        " sum(p.chosen) n_chosen FROM org_units u JOIN building_photos p"
        " ON p.unit_id=u.id GROUP BY u.id ORDER BY u.traeger, u.name")]


def photos_for_unit(conn, unit_id):
    return [dict(r) for r in conn.execute(
        "SELECT id, unit_id, path, perspective, style, rank, chosen, created"
        " FROM building_photos WHERE unit_id=? ORDER BY rank, id", (unit_id,))]


def update_photo(conn, photo_id, *, perspective=None, style=None, chosen=None):
    """Setzt einzelne Felder (nur die übergebenen). Unbekannter Style → ValueError (fail loud)."""
    sets, args = [], []
    if perspective is not None:
        sets.append("perspective=?")
        args.append(perspective or None)
    if style is not None:
        if style not in STYLES:
            raise ValueError(f"unbekannter Style {style!r}, erlaubt: {STYLES}")
        sets.append("style=?")
        args.append(style)
    if chosen is not None:
        sets.append("chosen=?")
        args.append(1 if chosen else 0)
    if not sets:
        return {"updated": 0}
    args.append(photo_id)
    cur = conn.execute(f"UPDATE building_photos SET {','.join(sets)} WHERE id=?", args)
    conn.commit()
    return {"updated": cur.rowcount}


def set_order(conn, unit_id, ordered_ids):
    """Drag-Reorder: schreibt rank = Position in ordered_ids (nur für diese Einheit)."""
    for rank, pid in enumerate(ordered_ids):
        conn.execute("UPDATE building_photos SET rank=? WHERE id=? AND unit_id=?",
                     (rank, int(pid), unit_id))
    conn.commit()
    return {"ordered": len(ordered_ids)}


def add_manual(conn, unit_id, path, *, style="photo", perspective=None):
    """Manuell ergänztes Foto registrieren (Datei liegt bereits unter out_root). Style aus STYLES."""
    if style not in STYLES:
        raise ValueError(f"unbekannter Style {style!r}, erlaubt: {STYLES}")
    nxt = conn.execute("SELECT coalesce(max(rank),-1)+1 r FROM building_photos"
                       " WHERE unit_id=?", (unit_id,)).fetchone()["r"]
    cur = conn.execute(
        "INSERT OR IGNORE INTO building_photos"
        "(unit_id, path, perspective, style, rank, chosen, created) VALUES (?,?,?,?,?,0,?)",
        (unit_id, path, perspective or None, style, nxt, _now()))
    conn.commit()
    return {"id": cur.lastrowid, "inserted": cur.rowcount}
