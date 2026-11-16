"""Disposable GFF query index outside the reference directory; sources are read-only."""
import hashlib
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import threading

from .gff3 import (Feature, Gff3, _open_text, _parse_attributes,
                  gene_attribute_keys, normalize_gene_key)

_BUILD_LOCK = threading.Lock()
_VERSION = "1"


def _signature(path):
    stat = path.stat()
    return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _cache_path(path):
    # Never place a derived index beside a user's genome or BLAST database.
    root = Path(os.environ.get("SEQWB_REFERENCE_CACHE_DIR") or
                Path.home() / ".cache" / "sequence-reference-queries")
    token = hashlib.sha256(repr((_VERSION, _signature(path))).encode()).hexdigest()
    return root / (token + ".sqlite")


def _build(path, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = _signature(path)
    fd, temporary = tempfile.mkstemp(prefix="building-", dir=str(destination.parent))
    os.close(fd)
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.executescript("""
                CREATE TABLE features(n INTEGER PRIMARY KEY, seqid TEXT,
                  start INTEGER, end INTEGER, kind TEXT, line TEXT);
                CREATE TABLE anchors(id TEXT PRIMARY KEY, n INTEGER);
                CREATE TABLE parents(parent TEXT, child INTEGER);
                CREATE TABLE aliases(key TEXT, n INTEGER);
            """)
            with _open_text(str(path)) as source:
                for line in source:
                    if line.startswith("##FASTA"):
                        break
                    if not line.strip() or line.startswith("#"):
                        continue
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) < 9:
                        continue
                    try:
                        start, end = int(parts[3]), int(parts[4])
                    except ValueError:
                        continue
                    attrs = _parse_attributes(parts[8])
                    cursor = db.execute("INSERT INTO features(seqid,start,end,kind,line) VALUES(?,?,?,?,?)",
                                        (parts[0], start, end, parts[2], line.rstrip("\r\n")))
                    n = cursor.lastrowid
                    if attrs.get("ID"):
                        db.execute("INSERT OR IGNORE INTO anchors VALUES(?,?)", (attrs["ID"], n))
                    for parent in attrs.get("Parent", "").split(","):
                        if parent.strip():
                            db.execute("INSERT INTO parents VALUES(?,?)", (parent.strip(), n))
                    if parts[2] == "gene":
                        db.executemany("INSERT INTO aliases VALUES(?,?)",
                                       [(key, n) for key in set(gene_attribute_keys(attrs))])
            db.executescript("""
                CREATE INDEX alias_key ON aliases(key);
                CREATE INDEX parent_key ON parents(parent);
                CREATE INDEX anchor_n ON anchors(n);
                CREATE INDEX region_key ON features(seqid,kind,start);
                PRAGMA user_version=1;
            """)
        if _signature(path) != before:
            raise RuntimeError("Annotation changed during indexing; retry with a stable input.")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _connect(path):
    path = Path(path)
    destination = _cache_path(path)
    with _BUILD_LOCK:
        if not destination.exists():
            _build(path, destination)
    return sqlite3.connect(destination.resolve().as_uri() + "?mode=ro", uri=True)


def _features(rows):
    features, anchors = [], {}
    for row in rows:
        parts = row[0].split("\t")
        feature = Feature(parts[0], parts[1], parts[2], int(parts[3]),
                          int(parts[4]), parts[6], _parse_attributes(parts[8]))
        features.append(feature)
        if feature.id:
            anchors.setdefault(feature.id, feature)
    for feature in features:
        for parent in (feature.parent or "").split(","):
            if parent.strip() in anchors:
                anchors[parent.strip()].children.append(feature)
    return Gff3(features)


def _family(db, roots_sql, parameters):
    rows = db.execute("""
        WITH RECURSIVE family(n) AS (
          """ + roots_sql + """
          UNION
          SELECT p.child FROM family f JOIN anchors a ON a.n=f.n
            JOIN parents p ON p.parent=a.id
        ) SELECT line FROM features WHERE n IN (SELECT n FROM family) ORDER BY n
    """, parameters)
    return _features(rows)


def gene_annotation(path, gene_id, seqid=None):
    """Retrieve only matching genes and their descendants; ambiguity is preserved."""
    with closing(_connect(path)) as db:
        sql = "SELECT DISTINCT f.line,f.n FROM aliases a JOIN features f ON f.n=a.n WHERE a.key=?"
        parameters = [normalize_gene_key(gene_id)]
        if seqid is not None:
            sql += " AND f.seqid=?"
            parameters.append(seqid)
        rows = list(db.execute(sql + " ORDER BY f.n", parameters))
        candidates = _features(rows)
        gene = candidates.gene(gene_id)
        if gene is None:
            return Gff3([])
        n = rows[candidates.features.index(gene)][1]
        return _family(db, "SELECT n FROM features WHERE n=?", (n,))


def region_annotation(path, seqid, start, end):
    """Retrieve complete overlapping gene models without loading a chromosome."""
    with closing(_connect(path)) as db:
        exists = db.execute("SELECT 1 FROM features WHERE seqid=? LIMIT 1", (seqid,)).fetchone()
        return _family(db,
            "SELECT n FROM features WHERE seqid=? AND kind='gene' AND start<=? AND end>=?",
            (seqid, end, start)), bool(exists)
