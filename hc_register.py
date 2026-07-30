#!/usr/bin/env python3
"""Load the extracted High Court corpus into the live register.

Why this exists rather than worker-src/loader.py: the register in production was
built by a different generation of loader, and its shape has moved on. Live
`judgment_acts` is (judgment_id, act_id, sections text[], raw) keyed on
(judgment_id, act_id); the repo loader writes a `mentions` column that the live
table does not have, so it fails on the first archive that carries any Act. The
register holds 363k Delhi judgments and ~397k Act links, so the schema is the
fixed point here and the loader is what bends. Column names and types below were
read off the live database, not assumed.

Idempotency: one archive is one transaction, and its `ingested` row is written
inside that transaction — so an archive is either fully present with its marker
or fully absent.

That alone is not enough here, and the first full run proved it. Supreme Court
filenames carry no date (`2024_9_770_773_EN.pdf`), so decision_date is NULL, and
NULL defeats the UNIQUE (cnr, order_no, decision_date) that guards the High
Court rows. Worse, upstream files the same PDF under more than one year — the
1971 volume also appears in the 1950 archive — so 20,033 rows arrived twice, and
in one case the second copy had extracted as empty. Deduping therefore runs on
(bench, cnr), which is the document's real identity, enforced by the partial
unique index judgments_sc_doc_uniq, and a collision keeps whichever copy
extracted fuller.

Env: INDEX_DB_URL, R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
     R2_BUCKET, SC_PREFIX (default judgment-corpus/v4-sc)
"""
import os, re, io, sys, gzip, json, time, zlib
import boto3
from botocore.config import Config
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker-src"))
from loader import canonical, parse_name  # noqa: E402

DSN    = os.environ["INDEX_DB_URL"]
BUCK   = os.environ.get("R2_BUCKET", "sharadmcp")
PREF   = os.environ.get("HC_PREFIX", "judgment-corpus/v4")
# Several loaders run at once, one per shard, so they never contend for the
# same archive. The split is a stable hash of the key, not a slice of a list —
# a list re-read after new archives land would reshuffle every assignment.
NSHARDS = int(os.environ.get("NSHARDS", "1"))
SHARD   = int(os.environ.get("SHARD", "0"))

R2 = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                  aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                  config=Config(connect_timeout=20, read_timeout=180,
                                retries={"max_attempts": 5, "mode": "standard"}))

def log(*a): print(time.strftime("[%H:%M:%S]"), *a, flush=True)

def keys():
    out, tok = [], None
    while True:
        kw = dict(Bucket=BUCK, Prefix=PREF + "/", MaxKeys=1000)
        if tok: kw["ContinuationToken"] = tok
        r = R2.list_objects_v2(**kw)
        out += [o["Key"] for o in r.get("Contents", []) if o["Key"].endswith(".jsonl.gz")]
        tok = r.get("NextContinuationToken")
        if not tok: break
    return sorted(out)

def db():
    return psycopg2.connect(DSN, connect_timeout=20)

def load_one(conn, key):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM ingested WHERE source_key=%s", (key,))
    if cur.fetchone():
        return 0, 0

    # judgment-corpus/v4/year=2015/court=7_26/bench=dhcdb.jsonl.gz
    # ...and for the incremental parts:
    # judgment-corpus/v4/year=2025/court=7_26/bench=dhcdb/part-<stamp>.tar.jsonl.gz
    year  = int(re.search(r"year=(\d{4})", key).group(1))
    court = re.search(r"court=([^/]+)", key).group(1)
    bench = re.search(r"bench=([^/.]+)", key).group(1)

    body = R2.get_object(Bucket=BUCK, Key=key)["Body"].read()

    jrows, per_doc = [], []
    for line in gzip.GzipFile(fileobj=io.BytesIO(body)):
        d = json.loads(line)
        # High Court filenames carry CNR, order number and date
        # (DLHC010667702015_1_2015-11-27.pdf), so the register's own
        # UNIQUE (cnr, order_no, decision_date) does the deduping here — unlike
        # the Supreme Court side, where the date is absent.
        cnr, order_no, ddate = parse_name(d["file"])
        marks = d.get("marks") or {}
        jrows.append((
            cnr, order_no, ddate, court, bench, year,
            d.get("kind"), d.get("chars", 0), d.get("thash"),
            bool(marks.get("cites_precedent")), bool(marks.get("interprets_law")),
            bool(marks.get("grants_relief")), bool(marks.get("mere_adjournment")),
            bool(marks.get("substantive")),
            f"data/pdf/year={year}/court={court}/bench={bench}/{d['file']}", key,
            d.get("order_rules") or [], d.get("schedules") or [],
        ))
        per_doc.append(((cnr, order_no, ddate), d.get("acts") or [],
                        d.get("bound") or {}, d.get("secs") or []))

    execute_values(cur, """INSERT INTO judgments
        (cnr,order_no,decision_date,court_code,bench,year,kind,n_chars,text_hash,
         cites_precedent,interprets_law,grants_relief,mere_adjournment,substantive,
         pdf_key,text_key,order_rules,schedules)
        VALUES %s ON CONFLICT (cnr,order_no,decision_date)
        DO UPDATE SET n_chars=EXCLUDED.n_chars, kind=EXCLUDED.kind,
                      text_hash=EXCLUDED.text_hash,
                      order_rules=EXCLUDED.order_rules, schedules=EXCLUDED.schedules
        WHERE EXCLUDED.n_chars > judgments.n_chars""",
        jrows, page_size=1000)

    cur.execute("""SELECT cnr,order_no,decision_date,id FROM judgments
                   WHERE court_code=%s AND year=%s AND bench=%s""", (court, year, bench))
    idmap = {(a, b, str(c) if c else None): i for a, b, c, i in cur.fetchall()}

    # Cache Act ids for this archive; the table is shared, so upsert rather than
    # assume a name is new.
    acache = {}
    def act_id(canon, cy, code):
        if canon in acache: return acache[canon]
        cur.execute("""INSERT INTO acts(canonical,act_year,short_code) VALUES (%s,%s,%s)
                       ON CONFLICT (canonical) DO UPDATE SET canonical=EXCLUDED.canonical
                       RETURNING id""", (canon, cy, code))
        acache[canon] = cur.fetchone()[0]
        return acache[canon]

    # Several surface spellings collapse to one canonical Act inside a single
    # judgment, so merge before insert — ON CONFLICT cannot resolve duplicates
    # arriving in the same statement.
    ja, js = {}, set()
    for ident, acts, bound, secs in per_doc:
        jid = idmap.get(ident)
        if not jid: continue
        for raw, _cnt in acts:
            canon, cy, code = canonical(raw)
            if not canon: continue
            aid = act_id(canon, cy, code)
            sect = sorted({s for a, ss in bound.items() if canonical(a)[0] == canon
                           for s in ss})
            prev = ja.get((jid, aid))
            if prev: prev[0].update(sect)
            else: ja[(jid, aid)] = [set(sect), raw]
        for s in secs:
            js.add((jid, s))

    if ja:
        execute_values(cur, """INSERT INTO judgment_acts(judgment_id,act_id,sections,raw)
            VALUES %s ON CONFLICT (judgment_id,act_id) DO UPDATE
            SET sections = (SELECT array_agg(DISTINCT x) FROM unnest(
                  judgment_acts.sections || EXCLUDED.sections) x)""",
            [(j, a, sorted(s), raw) for (j, a), (s, raw) in ja.items()], page_size=1000)
    if js:
        execute_values(cur, """INSERT INTO judgment_sections(judgment_id,section)
            VALUES %s ON CONFLICT DO NOTHING""", sorted(js), page_size=2000)

    cur.execute("INSERT INTO ingested(source_key,docs) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (key, len(jrows)))
    conn.commit(); cur.close()
    return len(jrows), len(ja)

def main():
    ks = keys()
    if NSHARDS > 1:
        ks = [k for k in ks if zlib.crc32(k.encode()) % NSHARDS == SHARD]
        log(f"shard {SHARD}/{NSHARDS}: {len(ks)} HC archives")
    else:
        log(f"{len(ks)} HC archives on R2")
    docs = links = done = 0
    for i, k in enumerate(ks, 1):
        for att in range(1, 5):
            try:
                conn = db()
                n, l = load_one(conn, k)
                conn.close()
                docs += n; links += l; done += 1
                break
            except Exception as e:
                try: conn.rollback(); conn.close()
                except Exception: pass
                log(f"  {k.split('/')[-2:]}: try {att} {type(e).__name__} {str(e)[:130]}")
                time.sleep(8 * att)
        if i % 10 == 0 or i == len(ks):
            log(f"progress {i}/{len(ks)} | {docs} docs | {links} act-links")
    log(f"HC REGISTER DONE | archives {done}/{len(ks)} | {docs} docs | {links} act-links")

if __name__ == "__main__":
    main()
