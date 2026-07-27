-- Judgment index. The TEXT itself lives in R2; this register only knows
-- what each judgment is, what it cites, and where its text sits.

CREATE TABLE IF NOT EXISTS judgments (
  id            BIGSERIAL PRIMARY KEY,
  cnr           TEXT NOT NULL,
  order_no      INT  NOT NULL DEFAULT 1,
  court_code    TEXT NOT NULL,          -- e.g. 7_26 (Delhi HC)
  bench         TEXT,
  year          INT,                    -- decision-date year (S3 partition)
  decision_date DATE,
  n_chars       INT,                    -- extracted text length; 0 => no text layer
  pdf_key       TEXT,                   -- key in the AWS open-data bucket
  text_key      TEXT,                   -- key in R2 where extracted text lives
  title         TEXT,
  judge         TEXT,
  disposal      TEXT,
  UNIQUE (cnr, order_no, decision_date)
);
CREATE INDEX IF NOT EXISTS idx_j_court_year ON judgments(court_code, year);
CREATE INDEX IF NOT EXISTS idx_j_date       ON judgments(decision_date);

-- Canonical Act registry. Aliases collapse the many spellings a judgment
-- may use ("Income Tax Act" / "Income-tax Act" / "Incometax Act").
CREATE TABLE IF NOT EXISTS acts (
  id            SERIAL PRIMARY KEY,
  canonical     TEXT UNIQUE NOT NULL,
  act_year      INT,
  short_code    TEXT,                   -- IPC, CrPC, NI, DV, POCSO, NDPS...
  mentions      BIGINT DEFAULT 0,       -- corpus-wide frequency
  judgments_cnt BIGINT DEFAULT 0
);
CREATE TABLE IF NOT EXISTS act_aliases (
  alias         TEXT PRIMARY KEY,       -- normalised surface form
  act_id        INT REFERENCES acts(id) ON DELETE CASCADE
);

-- What each judgment cites.
CREATE TABLE IF NOT EXISTS judgment_acts (
  judgment_id   BIGINT REFERENCES judgments(id) ON DELETE CASCADE,
  act_id        INT    REFERENCES acts(id) ON DELETE CASCADE,
  raw           TEXT,                   -- surface form as it appeared
  mentions      INT DEFAULT 1,
  PRIMARY KEY (judgment_id, act_id)
);
CREATE INDEX IF NOT EXISTS idx_ja_act ON judgment_acts(act_id);

CREATE TABLE IF NOT EXISTS judgment_sections (
  judgment_id   BIGINT REFERENCES judgments(id) ON DELETE CASCADE,
  section       TEXT NOT NULL,          -- "138", "482", "Art.226"
  PRIMARY KEY (judgment_id, section)
);
CREATE INDEX IF NOT EXISTS idx_js_sec ON judgment_sections(section);

-- Unresolved surface forms park here until the canonicaliser learns them.
CREATE TABLE IF NOT EXISTS act_unresolved (
  raw           TEXT PRIMARY KEY,
  mentions      BIGINT DEFAULT 0,
  judgments_cnt BIGINT DEFAULT 0
);

-- A "collection" is one chatbot's slice (e.g. DV Act). Rows here are the
-- work-list for the Gemini embedding pass, so a slice can be embedded once,
-- resumed after a failure, and re-embedded only when deliberately reset.
CREATE TABLE IF NOT EXISTS collections (
  id            SERIAL PRIMARY KEY,
  slug          TEXT UNIQUE NOT NULL,   -- 'dv-act', 'ni-138', 'pocso'
  label         TEXT NOT NULL,
  act_id        INT REFERENCES acts(id),
  section_filter TEXT[],                -- optional, e.g. {'138'}
  created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS collection_members (
  collection_id INT    REFERENCES collections(id) ON DELETE CASCADE,
  judgment_id   BIGINT REFERENCES judgments(id) ON DELETE CASCADE,
  embedded_at   TIMESTAMPTZ,            -- NULL => still to be embedded
  embed_error   TEXT,
  PRIMARY KEY (collection_id, judgment_id)
);
CREATE INDEX IF NOT EXISTS idx_cm_todo
  ON collection_members(collection_id) WHERE embedded_at IS NULL;

-- Which bench-year archives are already loaded (loader idempotency).
CREATE TABLE IF NOT EXISTS ingested (
  source_key    TEXT PRIMARY KEY,
  docs          INT,
  loaded_at     TIMESTAMPTZ DEFAULT now()
);
