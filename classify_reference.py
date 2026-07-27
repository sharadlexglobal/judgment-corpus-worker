import re,glob,os,collections

RESERVED = re.compile(r'RESERVED\s+ON|PRONOUNCED\s+ON|DATE\s+OF\s+RESERV|Judgment\s+reserved|Judgment\s+delivered|DATE\s+OF\s+DECISION', re.I)
HDR_JUDG = re.compile(r'^\s*(?:JUDGMENT|J\s*U\s*D\s*G\s*M\s*E\s*N\s*T)\s*$', re.I|re.M)
REGISTRAR= re.compile(r'CORAM:.{0,80}REGISTRAR', re.I|re.S)
JUSTICE  = re.compile(r"HON'?BLE\s+(?:MR\.?|MS\.?|MRS\.?|DR\.?)?\s*JUSTICE", re.I)
PARA     = re.compile(r'^\s{0,6}(\d{1,3})\.\s', re.M)
PAGES    = re.compile(r'Page\s+\d+\s+of\s+(\d+)', re.I)
REASON   = re.compile(r'\b(?:we are of the (?:considered )?(?:view|opinion)|in our (?:considered )?(?:view|opinion)|'
                      r'it is well settled|held that|the ratio|laid down in|relied upon|'
                      r'having considered|for the foregoing reasons|in the light of the above)\b', re.I)
PRECED   = re.compile(r'\b\(\d{4}\)\s*\d+\s*SCC\b|\bAIR\s+\d{4}\s+SC\b|\b\d{4}\s+SCC\s+\d+', re.I)
LISTING  = re.compile(r'\b(?:list (?:the matter|it) (?:on|before)|renotify|re-notify|adjourn|'
                      r'stand(?:s)? over|next date of hearing|for arguments on)\b', re.I)

def features(t):
    paras=[int(m.group(1)) for m in PARA.finditer(t)]
    pages=[int(m.group(1)) for m in PAGES.finditer(t)]
    return {
      'chars':len(t),
      'reserved':bool(RESERVED.search(t)),
      'hdr_judgment':bool(HDR_JUDG.search(t)),
      'registrar':bool(REGISTRAR.search(t)),
      'justice':bool(JUSTICE.search(t)),
      'max_para':max(paras) if paras else 0,
      'pages':max(pages) if pages else 0,
      'reasoning':len(REASON.findall(t)),
      'precedents':len(PRECED.findall(t)),
      'listing':len(LISTING.findall(t)),
    }

def score(f):
    """Positive => reasoned judgment. Built from the signals a lawyer would use."""
    s = 0
    if f['registrar']:      s -= 6          # a Registrar never delivers a judgment
    if f['reserved']:       s += 5          # reserved-and-pronounced is the strongest tell
    if f['hdr_judgment']:   s += 4
    if f['justice']:        s += 1
    if f['max_para'] >= 10: s += 3
    elif f['max_para'] >= 5:s += 1
    if f['pages'] >= 10:    s += 2
    elif f['pages'] >= 5:   s += 1
    s += min(f['reasoning'], 4)
    s += min(f['precedents'], 3)
    s -= min(f['listing'], 3)
    if f['chars'] < 2000:   s -= 3
    elif f['chars'] > 20000:s += 2
    return s

def label(s): return 'JUDGMENT' if s >= 6 else ('ORDER' if s >= 1 else 'PARCHI')

if __name__ == '__main__':
    rows=[]
    for p in sorted(glob.glob('samp/*.txt')):
        bucket=os.path.basename(p).split('__')[0]
        t=open(p,errors='ignore').read()
        f=features(t); s=score(f)
        rows.append((bucket,s,label(s),f))
    print(f"{'bucket':6} {'score':>5} {'verdict':9} {'chars':>7} {'para':>4} {'pg':>3} {'rsvd':>4} {'reg':>3} {'reas':>4} {'prec':>4} {'list':>4}")
    for b,s,l,f in rows:
        print(f"{b:6} {s:5} {l:9} {f['chars']:7} {f['max_para']:4} {f['pages']:3} "
              f"{'Y' if f['reserved'] else '.':>4} {'Y' if f['registrar'] else '.':>3} "
              f"{f['reasoning']:4} {f['precedents']:4} {f['listing']:4}")
    print()
    cross=collections.Counter((b,l) for b,s,l,f in rows)
    print('LENGTH-BUCKET  x  TEXT-VERDICT:')
    for b in ['tiny','small','mid','big','huge']:
        line=' '.join(f"{l}={cross[(b,l)]}" for l in ['PARCHI','ORDER','JUDGMENT'] if cross[(b,l)])
        print(f'  {b:6} {line}')
