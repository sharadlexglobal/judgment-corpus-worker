#!/usr/bin/env python3
"""
Load extractor output (gzipped JSONL on R2) into the judgment index database.

Idempotent: a source key already in `ingested` is skipped, so this can be
re-run freely as the extractor produces more bench-years.

Act names are canonicalised on the way in — the many surface spellings of one
Act collapse to a single row, and anything unrecognised is parked in
`act_unresolved` so the alias table can be grown from real evidence rather
than guesswork.
"""
import os, re, gzip, json, io, sys, time
import boto3, psycopg2
from psycopg2.extras import execute_values

R2   = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
                    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"])
BUCK = os.environ.get("R2_BUCKET", "sharadmcp")
PREF = os.environ.get("OUT_PREFIX", "judgment-corpus/v1")
DSN  = os.environ["INDEX_DB_URL"]

def log(*a): print(time.strftime("[%H:%M:%S]"), *a, flush=True)

# ---------- canonicalisation ----------
SHORT = {
    "indian penal code": ("Indian Penal Code, 1860", 1860, "IPC"),
    "penal code": ("Indian Penal Code, 1860", 1860, "IPC"),
    "ipc": ("Indian Penal Code, 1860", 1860, "IPC"),
    "code of criminal procedure": ("Code of Criminal Procedure, 1973", 1973, "CrPC"),
    "criminal procedure code": ("Code of Criminal Procedure, 1973", 1973, "CrPC"),
    "crpc": ("Code of Criminal Procedure, 1973", 1973, "CrPC"),
    "code of civil procedure": ("Code of Civil Procedure, 1908", 1908, "CPC"),
    "civil procedure code": ("Code of Civil Procedure, 1908", 1908, "CPC"),
    "cpc": ("Code of Civil Procedure, 1908", 1908, "CPC"),
    "negotiable instruments act": ("Negotiable Instruments Act, 1881", 1881, "NI"),
    "ni act": ("Negotiable Instruments Act, 1881", 1881, "NI"),
    "protection of women from domestic violence act":
        ("Protection of Women from Domestic Violence Act, 2005", 2005, "DV"),
    "domestic violence act": ("Protection of Women from Domestic Violence Act, 2005", 2005, "DV"),
    "dv act": ("Protection of Women from Domestic Violence Act, 2005", 2005, "DV"),
    "protection of children from sexual offences act": ("POCSO Act, 2012", 2012, "POCSO"),
    "pocso act": ("POCSO Act, 2012", 2012, "POCSO"),
    "narcotic drugs and psychotropic substances act": ("NDPS Act, 1985", 1985, "NDPS"),
    "ndps act": ("NDPS Act, 1985", 1985, "NDPS"),
    "indian evidence act": ("Indian Evidence Act, 1872", 1872, "EVID"),
    "evidence act": ("Indian Evidence Act, 1872", 1872, "EVID"),
    "income tax act": ("Income-tax Act, 1961", 1961, "IT"),
    "income-tax act": ("Income-tax Act, 1961", 1961, "IT"),
    "incometax act": ("Income-tax Act, 1961", 1961, "IT"),
    "it act": ("Income-tax Act, 1961", 1961, "IT"),
    "prevention of corruption act": ("Prevention of Corruption Act, 1988", 1988, "PC"),
    "corruption act": ("Prevention of Corruption Act, 1988", 1988, "PC"),
    "pc act": ("Prevention of Corruption Act, 1988", 1988, "PC"),
    "arbitration and conciliation act": ("Arbitration and Conciliation Act, 1996", 1996, "ARB"),
    "conciliation act": ("Arbitration and Conciliation Act, 1996", 1996, "ARB"),
    "companies act": ("Companies Act", None, "COS"),
    "constitution of india": ("Constitution of India, 1950", 1950, "CONST"),
    "motor vehicles act": ("Motor Vehicles Act, 1988", 1988, "MV"),
    "motor vehicle act": ("Motor Vehicles Act, 1988", 1988, "MV"),
    "arms act": ("Arms Act, 1959", 1959, "ARMS"),
    "limitation act": ("Limitation Act, 1963", 1963, "LIM"),
    "industrial disputes act": ("Industrial Disputes Act, 1947", 1947, "ID"),
    "land acquisition act": ("Land Acquisition Act, 1894", 1894, "LA"),
    "essential commodities act": ("Essential Commodities Act, 1955", 1955, "EC"),
}

def norm(s):
    s = re.sub(r"\s+", " ", str(s)).strip()
    s = re.sub(r"[.,;:()\[\]\"'’‘]", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"^(the|under|of|in)\s+", "", s)
    return s

def split_year(s):
    m = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b\s*$", s.strip())
    y = int(m.group(1)) if m else None
    base = re.sub(r"[,\s]*\b(1[6-9]\d{2}|20\d{2})\b\s*$", "", s).strip()
    return base, y

def canonical(raw):
    base, yr = split_year(raw)
    n = norm(base)
    if n in SHORT:
        c, cy, code = SHORT[n]
        return c, cy or yr, code
    for alias, (c, cy, code) in SHORT.items():
        if len(n) > 8 and (n.endswith(alias) or alias.endswith(n)):
            return c, cy or yr, code
    return None, yr, None

# ---------- filename -> identity ----------
FN = re.compile(r"^([A-Z]{2,6}\d{6,}\w*?)_(\d+)_(\d{4}-\d{2}-\d{2})\.pdf$", re.I)

def parse_name(fn):
    m = FN.match(fn)
    if m:
        return m.group(1), int(m.group(2)), m.group(3)
    return os.path.splitext(fn)[0], 1, None

def list_outputs():
    keys, tok = [], None
    while True:
        kw = dict(Bucket=BUCK, Prefix=PREF + "/", MaxKeys=1000)
        if tok: kw["ContinuationToken"] = tok
        r = R2.list_objects_v2(**kw)
        keys += [o["Key"] for o in r.get("Contents", []) if o["Key"].endswith(".jsonl.gz")]
        tok = r.get("NextContinuationToken")
        if not tok: break
    return sorted(keys)

def ensure_act(cur, cache, canon, cy, code):
    if canon in cache: return cache[canon]
    cur.execute("""INSERT INTO acts(canonical, act_year, short_code) VALUES (%s,%s,%s)
                   ON CONFLICT (canonical) DO UPDATE SET canonical=EXCLUDED.canonical
                   RETURNING id""", (canon, cy, code))
    aid = cur.fetchone()[0]
    cache[canon] = aid
    return aid

def load_one(conn, key, cache):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM ingested WHERE source_key=%s", (key,))
    if cur.fetchone():
        log("skip (loaded):", key); return 0
    # judgment-corpus/v1/year=2015/court=7_26/bench=dhcdb.jsonl.gz
    year  = int(re.search(r"year=(\d{4})", key).group(1))
    court = re.search(r"court=([^/]+)", key).group(1)
    bench = re.search(r"bench=([^/.]+)", key).group(1)
    body = R2.get_object(Bucket=BUCK, Key=key)["Body"].read()
    rows, acts_rows, sec_rows, unres = [], [], [], {}
    n = 0
    for line in gzip.GzipFile(fileobj=io.BytesIO(body)):
        d = json.loads(line)
        cnr, order_no, ddate = parse_name(d["file"])
        rows.append((cnr, order_no, court, bench, year, ddate, d.get("chars", 0),
                     f"data/pdf/year={year}/court={court}/bench={bench}/{d['file']}", key))
        n += 1
        for raw, cnt in d.get("acts", []):
            c, cy, code = canonical(raw)
            if c: acts_rows.append((cnr, order_no, ddate, c, cy, code, raw, cnt))
            else: unres[raw] = unres.get(raw, 0) + cnt
        for s in d.get("secs", []):
            sec_rows.append((cnr, order_no, ddate, s))
    execute_values(cur, """INSERT INTO judgments
        (cnr,order_no,court_code,bench,year,decision_date,n_chars,pdf_key,text_key)
        VALUES %s ON CONFLICT (cnr,order_no,decision_date) DO NOTHING""", rows, page_size=2000)
    cur.execute("""SELECT cnr,order_no,decision_date,id FROM judgments
                   WHERE court_code=%s AND year=%s AND bench=%s""", (court, year, bench))
    idmap = {(a, b, str(c) if c else None): i for a, b, c, i in cur.fetchall()}
    # Several surface spellings in one judgment collapse to the same Act, so
    # merge per (judgment, act) before insert — ON CONFLICT cannot resolve
    # duplicates that arrive inside a single statement.
    agg = {}
    for cnr, o, dd, c, cy, code, raw, cnt in acts_rows:
        jid = idmap.get((cnr, o, dd))
        if not jid:
            continue
        aid = ensure_act(cur, cache, c, cy, code)
        k = (jid, aid)
        if k in agg:
            agg[k][1] += cnt
        else:
            agg[k] = [raw, cnt]
    ja = [(jid, aid, raw, cnt) for (jid, aid), (raw, cnt) in agg.items()]
    execute_values(cur, """INSERT INTO judgment_acts(judgment_id,act_id,raw,mentions)
        VALUES %s ON CONFLICT (judgment_id,act_id)
        DO UPDATE SET mentions=judgment_acts.mentions+EXCLUDED.mentions""", ja, page_size=2000)
    js = sorted({(idmap[(c, o, dd)], s) for c, o, dd, s in sec_rows if (c, o, dd) in idmap})
    execute_values(cur, """INSERT INTO judgment_sections(judgment_id,section)
        VALUES %s ON CONFLICT DO NOTHING""", js, page_size=5000)
    if unres:
        execute_values(cur, """INSERT INTO act_unresolved(raw,mentions) VALUES %s
            ON CONFLICT (raw) DO UPDATE SET mentions=act_unresolved.mentions+EXCLUDED.mentions""",
            list(unres.items()), page_size=2000)
    cur.execute("INSERT INTO ingested(source_key,docs) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (key, n))
    conn.commit(); cur.close()
    log(f"loaded {key} | {n} judgments | {len(ja)} act-links | {len(js)} section-links | "
        f"{len(unres)} unresolved names")
    return n

def main():
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute(open(os.path.join(os.path.dirname(__file__), "schema.sql")).read())
    conn.commit(); cur.close()
    cache = {}
    keys = list_outputs()
    log(f"{len(keys)} extractor outputs on R2")
    total = 0
    for k in keys:
        try: total += load_one(conn, k, cache)
        except Exception as e:
            conn.rollback(); log("ERROR", k, type(e).__name__, str(e)[:200])
    cur = conn.cursor()
    cur.execute("""UPDATE acts a SET mentions=s.m, judgments_cnt=s.c FROM
        (SELECT act_id, SUM(mentions) m, COUNT(*) c FROM judgment_acts GROUP BY act_id) s
        WHERE a.id=s.act_id""")
    conn.commit()
    cur.execute("""SELECT a.canonical, a.judgments_cnt FROM acts a
                   ORDER BY a.judgments_cnt DESC NULLS LAST LIMIT 25""")
    log(f"TOTAL loaded this run: {total}")
    log("TOP ACTS BY JUDGMENT COUNT:")
    for c, n in cur.fetchall(): log(f"   {n:7} {c}")
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
