# judgment-corpus-worker

Streams Indian High Court judgment archives from the AWS Open Data bucket
`s3://indian-high-court-judgments` (CC-BY-4.0), extracts the text with
`pdftotext`, pulls out the Acts / Sections / Articles each judgment cites by
pattern, and writes one gzipped JSONL per bench-year to Cloudflare R2.

Attribution: *Indian High Court Judgments*, accessed from
https://registry.opendata.aws/indian-high-court-judgments — CC-BY-4.0.

## Env
| var | meaning |
|---|---|
| `COURTS` | comma-separated court codes, e.g. `7_26` (Delhi) |
| `YEAR_FROM` / `YEAR_TO` | year range |
| `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | output store |
| `OUT_PREFIX` | key prefix for output |

Resumable: a bench-year whose output already exists in R2 is skipped.
