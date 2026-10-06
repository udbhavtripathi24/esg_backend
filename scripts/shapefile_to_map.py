#!/usr/bin/env python3
"""Convert an ESRI shapefile of district boundaries into the simplified
GeoJSON the ESG dashboard map renders, plus the backend district index.

Why this exists
---------------
Official boundary data -- Survey of India, state GIS departments, World
Bank extracts -- is distributed as shapefiles, not GeoJSON. A browser
cannot read a shapefile: it is a binary format, and it is not even one
file (.shp holds geometry, .dbf holds the attribute table with the
district names, .shx is the index, .prj records the coordinate system).
So the conversion has to happen server-side, once, whenever the boundary
source changes.

This is deliberately a script rather than an API endpoint. Boundary data
changes once in several years; running a conversion on every page load
would be cost with no benefit, and it would put a heavy dependency in
the request path.

Usage
-----
    python scripts/shapefile_to_map.py districts.zip --out-dir ./out
    python scripts/shapefile_to_map.py /path/to/folder --out-dir ./out

It writes two files:

    indiaDistricts.json   -> app4/src/data/      (the map renders this)
    _india_districts.json -> app/services/       (the API indexes this)

Both are produced from the same source, so the data and the map can
never disagree about which districts exist.

What it handles
---------------
* .zip archives or an unpacked folder
* Reprojection to WGS84 (lat/lon) when the .prj says the data is in a
  projected system -- Indian official data is often in a UTM zone or a
  Lambert Conformal Conic, and plotting those numbers as if they were
  degrees puts the map somewhere in the Atlantic.
* Attribute names vary between sources (DISTRICT, dtname, district,
  NAME_2 ...), so the district and state columns are detected rather
  than assumed.
* Features that carry a state name but no district name are whole-state
  outlines. They are written to a separate layer: drawn in the district
  layer they paint a solid shape over that state's own districts.
* Geometry simplification, because raw official boundaries are tens of
  megabytes and unusable in a browser.
"""
import argparse
import json
import math
import os
import sys
import tempfile
import zipfile

try:
    import shapefile  # pyshp
except ImportError:
    sys.exit("pyshp is required:  pip install pyshp")

try:
    from pyproj import CRS, Transformer
    HAVE_PYPROJ = True
except ImportError:
    HAVE_PYPROJ = False


# Attribute names seen across Survey of India, Census, GADM and World
# Bank extracts. Checked in order, case-insensitively.
DISTRICT_FIELDS = ["district", "dtname", "dist_name", "distname", "name_2",
                   "d_name", "dt_name", "nm_dist", "censuscode_name"]
STATE_FIELDS = ["st_nm", "state", "stname", "st_name", "name_1",
                "state_name", "statename", "s_name"]

EPS = 0.012        # simplification tolerance in degrees, ~1.3 km
MIN_AREA = 0.0012  # drop rings that render as a single pixel


def find_field(fields, candidates):
    lower = {f.lower(): f for f in fields}
    for c in candidates:
        if c in lower:
            return lower[c]
    # fall back to a fuzzy contains match
    for c in candidates:
        for low, orig in lower.items():
            if c in low:
                return orig
    return None


def rdp(pts, eps):
    """Ramer-Douglas-Peucker. Keeps the shape, drops redundant vertices."""
    if len(pts) < 3:
        return pts

    def perp(p, a, b):
        (x, y), (x1, y1), (x2, y2) = p, a, b
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return math.hypot(x - x1, y - y1)
        t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))

    dmax, idx = 0, 0
    for i in range(1, len(pts) - 1):
        d = perp(pts[i], pts[0], pts[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return rdp(pts[:idx + 1], eps)[:-1] + rdp(pts[idx:], eps)
    return [pts[0], pts[-1]]


def ring_area(r):
    s = 0
    for i in range(len(r) - 1):
        s += r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1]
    return abs(s) / 2


def simp_ring(r, force=False):
    r = [[round(x, 3), round(y, 3)] for x, y in r]
    out = [r[0]]
    for p in r[1:]:
        if p != out[-1]:
            out.append(p)
    if len(out) < 4:
        return None
    out = rdp(out, EPS)
    if len(out) < 4:
        if not force:
            return None
        # Too small for this tolerance: keep a coarse box so a tiny
        # territory still appears rather than silently vanishing.
        xs = [p[0] for p in r]
        ys = [p[1] for p in r]
        out = [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]]
    if out[0] != out[-1]:
        out.append(out[0])
    if ring_area(out) < MIN_AREA and not force:
        return None
    return out


def simp_poly(poly, force=False):
    rings = [r for r in (simp_ring(x) for x in poly) if r]
    if not rings and force:
        big = max(poly, key=lambda r: ring_area([[round(x, 3), round(y, 3)] for x, y in r]))
        r = simp_ring(big, force=True)
        rings = [r] if r else []
    return rings or None


def simplify_geometry(geom):
    t = geom["type"]
    if t == "Polygon":
        ng = simp_poly(geom["coordinates"]) or simp_poly(geom["coordinates"], force=True)
        return {"type": "Polygon", "coordinates": ng} if ng else None
    if t == "MultiPolygon":
        polys = [q for q in (simp_poly(x) for x in geom["coordinates"]) if q]
        if not polys:
            big = max(geom["coordinates"],
                      key=lambda poly: sum(ring_area([[round(x, 3), round(y, 3)] for x, y in r]) for r in poly))
            q = simp_poly(big, force=True)
            polys = [q] if q else []
        return {"type": "MultiPolygon", "coordinates": polys} if polys else None
    return None


def build_transformer(shp_base):
    """Return a lon/lat transform, or None if the data is already WGS84."""
    prj_path = shp_base + ".prj"
    if not os.path.exists(prj_path):
        print("  ! no .prj file -- assuming coordinates are already lon/lat (WGS84).")
        print("    If the map comes out blank or in the wrong place, the source")
        print("    is projected and the .prj is needed to convert it.")
        return None
    if not HAVE_PYPROJ:
        print("  ! .prj present but pyproj is not installed; cannot reproject.")
        print("    pip install pyproj")
        return None
    with open(prj_path) as fh:
        wkt = fh.read()
    crs = CRS.from_wkt(wkt)
    if crs.to_epsg() == 4326 or crs.is_geographic:
        print(f"  CRS: {crs.name} (already geographic, no reprojection needed)")
        return None
    print(f"  CRS: {crs.name} -> reprojecting to WGS84")
    return Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)


def reproject(geom, tf):
    if tf is None:
        return geom

    def ring(r):
        return [list(tf.transform(x, y)) for x, y in r]

    if geom["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [ring(r) for r in geom["coordinates"]]}
    return {"type": "MultiPolygon",
            "coordinates": [[ring(r) for r in poly] for poly in geom["coordinates"]]}


def locate_shp(path):
    """Accept a .zip, a folder, or a direct .shp path."""
    if path.lower().endswith(".zip"):
        tmp = tempfile.mkdtemp(prefix="shp_")
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        path = tmp
    if os.path.isdir(path):
        hits = []
        for root, _dirs, files in os.walk(path):
            for f in files:
                if f.lower().endswith(".shp"):
                    hits.append(os.path.join(root, f))
        if not hits:
            sys.exit(f"No .shp file found under {path}")
        if len(hits) > 1:
            print("Multiple shapefiles found; using the largest:")
            for h in hits:
                print("   ", h)
            hits.sort(key=os.path.getsize, reverse=True)
        return hits[0]
    return path


def main():
    ap = argparse.ArgumentParser(description="Convert a district shapefile into the dashboard map data.")
    ap.add_argument("source", help=".zip archive, folder, or .shp file")
    ap.add_argument("--out-dir", default=".", help="where to write the two JSON files")
    ap.add_argument("--district-field", help="override the district name column")
    ap.add_argument("--state-field", help="override the state name column")
    args = ap.parse_args()

    shp_path = locate_shp(args.source)
    base = os.path.splitext(shp_path)[0]
    print(f"Reading {shp_path}")

    for ext in (".dbf", ".shx"):
        if not os.path.exists(base + ext):
            sys.exit(f"Missing {base + ext}. A shapefile needs .shp, .dbf and .shx together — "
                     f"make sure all parts were supplied, not just the .shp.")

    tf = build_transformer(base)
    sf = shapefile.Reader(shp_path)
    fields = [f[0] for f in sf.fields[1:]]
    print(f"  attributes: {', '.join(fields)}")

    dfield = args.district_field or find_field(fields, DISTRICT_FIELDS)
    sfield = args.state_field or find_field(fields, STATE_FIELDS)
    if not dfield:
        sys.exit(f"Could not identify the district column among: {fields}\n"
                 f"Pass it explicitly with --district-field")
    if not sfield:
        sys.exit(f"Could not identify the state column among: {fields}\n"
                 f"Pass it explicitly with --state-field")
    print(f"  district column: {dfield}   state column: {sfield}")

    districts, states, skipped = [], [], 0
    for rec in sf.iterShapeRecords():
        geom = rec.shape.__geo_interface__
        if geom["type"] not in ("Polygon", "MultiPolygon"):
            skipped += 1
            continue
        geom = reproject(geom, tf)
        geom = simplify_geometry(geom)
        if not geom:
            skipped += 1
            continue
        atts = rec.record.as_dict()
        dname = (atts.get(dfield) or "").strip()
        sname = (atts.get(sfield) or "Unknown").strip()
        if dname:
            districts.append({"type": "Feature",
                              "properties": {"d": dname, "s": sname},
                              "geometry": geom})
        else:
            # State outline rather than a district.
            states.append({"type": "Feature",
                           "properties": {"s": sname},
                           "geometry": geom})

    if not districts:
        sys.exit("No district features were produced — check the --district-field setting.")

    # Sanity check: anything outside India's bounding box means the
    # reprojection is wrong, and the map would render somewhere absurd.
    lons, lats = [], []
    for f in districts:
        polys = [f["geometry"]["coordinates"]] if f["geometry"]["type"] == "Polygon" else f["geometry"]["coordinates"]
        for poly in polys:
            for ring in poly:
                for x, y in ring:
                    lons.append(x)
                    lats.append(y)
    print(f"  extent: lon {min(lons):.2f}..{max(lons):.2f}  lat {min(lats):.2f}..{max(lats):.2f}")
    if not (60 < min(lons) < 100 and 5 < min(lats) < 40):
        print("  ! WARNING: coordinates fall outside India's expected range.")
        print("    The source is probably projected and the .prj was missing or unreadable.")

    os.makedirs(args.out_dir, exist_ok=True)
    map_path = os.path.join(args.out_dir, "indiaDistricts.json")
    with open(map_path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": districts,
                   "states": {"type": "FeatureCollection", "features": states}},
                  fh, separators=(",", ":"))

    index = {}
    for f in districts:
        index.setdefault(f["properties"]["s"], set()).add(f["properties"]["d"])
    index = {k: sorted(v) for k, v in sorted(index.items())}
    idx_path = os.path.join(args.out_dir, "_india_districts.json")
    with open(idx_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, separators=(",", ":"))

    print(f"\n  districts:      {len(districts)} shapes, {sum(len(v) for v in index.values())} unique names")
    print(f"  state outlines: {len(states)}")
    print(f"  states/UTs:     {len(index)}")
    if skipped:
        print(f"  skipped:        {skipped} features (non-polygon or degenerate)")
    print(f"\n  wrote {map_path}  ({os.path.getsize(map_path)/1024:.0f} KB)")
    print(f"  wrote {idx_path}  ({os.path.getsize(idx_path)/1024:.0f} KB)")
    print("\nCopy them into place:")
    print(f"  cp {map_path}  <frontend>/src/data/indiaDistricts.json")
    print(f"  cp {idx_path}  <backend>/app/services/_india_districts.json")


if __name__ == "__main__":
    main()
