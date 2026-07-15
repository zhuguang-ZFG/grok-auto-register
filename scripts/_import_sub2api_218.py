#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import sub2api accounts json into chatgpt_auths with dedup."""
import json, time, sys
from pathlib import Path

zip_path = r'D:\Downloads\2026-07-15_22-32-11.json.zip'
AUTH_DIR = Path('chatgpt_auths')

import zipfile
zf = zipfile.ZipFile(zip_path)
with zf.open('2026-07-15_22-32-11.json') as f:
    data = json.load(f)
accts = data['accounts']

existing = set()
for p in AUTH_DIR.glob('*.json'):
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
        em = d.get('email') or (d.get('extra', {}) or {}).get('email')
        if em:
            existing.add(em.lower())
    except Exception:
        pass

new_ct = dup_ct = bad_ct = 0
plan_breakdown = {}
now = int(time.time())
exp_buckets = {'expired': 0, '<7d': 0, '<30d': 0, '>30d': 0}

for a in accts:
    creds = a.get('credentials', {}) or {}
    extra = a.get('extra', {}) or {}
    email = a.get('email') or creds.get('email') or extra.get('email')
    if not email:
        bad_ct += 1
        continue
    email = email.lower()
    plan = a.get('plan_type') or creds.get('plan_type') or extra.get('plan_type') or 'unknown'
    plan_breakdown[plan] = plan_breakdown.get(plan, 0) + 1

    exp = a.get('expires_at') or creds.get('expires_at') or 0
    try:
        exp = int(exp)
    except Exception:
        exp = 0
    if exp > 0:
        remaining = exp - now
        if remaining < 0:
            exp_buckets['expired'] += 1
        elif remaining < 604800:
            exp_buckets['<7d'] += 1
        elif remaining < 2592000:
            exp_buckets['<30d'] += 1
        else:
            exp_buckets['>30d'] += 1

    if email in existing:
        dup_ct += 1
        continue

    safe_email = email.replace('/', '_').replace('\\', '_')
    out = dict(a)
    out['email'] = email
    out['source'] = 'sub2api_2026-07-15_22-32-11'
    out['imported_at'] = now
    fname = safe_email + '.json'
    (AUTH_DIR / fname).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    new_ct += 1
    existing.add(email)

pool_after = len(list(AUTH_DIR.glob('*.json')))
print('new={} dup={} bad={} total_in_zip={}'.format(new_ct, dup_ct, bad_ct, len(accts)))
print('plan_breakdown={}'.format(plan_breakdown))
print('expiry={}'.format(exp_buckets))
print('pool_size_after={}'.format(pool_after))
