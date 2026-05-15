"""
One-shot, idempotent setup for per-row Comments + Last-Comment summary on
the InventoryTrack Projections table. Safe to re-run.

What this builds:
  Projection Comments (bpt35zccg) — already related to Projections via fid 7
    + Flag                  text-multiple-choice
    + Sortable_Last_Note    text formula (helper)
    + Last_Note_Display     text formula — outputs ONLY for the newest comment
                            per parent (the row whose Date Created equals
                            the parent's Last Comment Date)
  Projections (bpd237tvm) — summary fields via the existing relationship
    + Last Comment Date     timestamp summary, MAX of [Date Created]
    + Last Comment          text summary, COMBINED-TEXT of Last_Note_Display
                            (effectively the most recent comment, formatted
                            as "yyyy-MM-dd HH:mm - Owner [Flag]: note...")
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import os, requests

REALM  = 'pim.quickbase.com'
TOKEN  = os.environ.get('QB_TOKEN', 'b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s')
T_PROJ = 'bpd237tvm'   # Projections (parent)
T_COMM = 'bpt35zccg'   # Projection Comments (child)
REL_ID = 7             # existing parent->child relationship

H = {'QB-Realm-Hostname': REALM,
     'Authorization': f'QB-USER-TOKEN {TOKEN}',
     'Content-Type': 'application/json'}
API = 'https://api.quickbase.com/v1'


def qb(method, path, **kw):
    r = requests.request(method, API+path, headers=H, **kw)
    if r.status_code >= 300:
        print(f'  HTTP {r.status_code} {method} {path}\n  body: {r.text[:600]}')
        r.raise_for_status()
    return r.json() if r.text else {}


def find_field(tbl, label):
    for f in qb('GET', f'/fields?tableId={tbl}'):
        if f['label'].strip().lower() == label.strip().lower():
            return f
    return None


def find_lookup_of(tbl, target_fid):
    """Return the field on tbl that is a lookup of target_fid on the parent."""
    for f in qb('GET', f'/fields?tableId={tbl}'):
        p = f.get('properties', {}) or {}
        if p.get('lookupTargetFieldId') == target_fid:
            return f
    return None


def ensure_field(tbl, payload):
    label = payload['label']
    existing = find_field(tbl, label)
    if existing:
        print(f'  ok  {label!r} already exists (fid {existing["id"]})')
        return existing['id']
    res = qb('POST', f'/fields?tableId={tbl}', json=payload)
    print(f'  +   created {label!r} (fid {res["id"]})')
    return res['id']


def ensure_summary(label, summary_fid, accumulation_type):
    """Add a summary field on the PARENT via the existing relationship."""
    existing = find_field(T_PROJ, label)
    if existing:
        print(f'  ok  parent summary {label!r} exists (fid {existing["id"]})')
        return existing['id']
    res = qb('POST', f'/tables/{T_COMM}/relationship/{REL_ID}', json={
        'summaryFields': [{
            'label': label,
            'summaryFid': summary_fid,
            'accumulationType': accumulation_type,
        }]
    })
    # The response includes ALL summaries on the relationship; find ours by label
    for sf in res.get('summaryFields', []):
        if sf.get('label') == label:
            print(f'  +   created parent summary {label!r} (fid {sf["id"]})')
            return sf['id']
    # Fallback: re-query
    f = find_field(T_PROJ, label)
    if f:
        print(f'  +   created parent summary {label!r} (fid {f["id"]})')
        return f['id']
    raise RuntimeError(f'Failed to create summary {label}')


def ensure_lookup_back(parent_fid, target_label_hint):
    """Add a lookup field on the CHILD pulling parent_fid back. Idempotent."""
    existing = find_lookup_of(T_COMM, parent_fid)
    if existing:
        print(f'  ok  child lookup of fid {parent_fid} exists (fid {existing["id"]} {existing["label"]!r})')
        return existing['id'], existing['label']
    before = {f['id'] for f in qb('GET', f'/fields?tableId={T_COMM}')}
    qb('POST', f'/tables/{T_COMM}/relationship/{REL_ID}',
       json={'lookupFieldIds': [parent_fid]})
    new = find_lookup_of(T_COMM, parent_fid)
    if not new:
        raise RuntimeError(f'Could not find lookup of fid {parent_fid} after create')
    print(f'  +   created child lookup of fid {parent_fid} (fid {new["id"]} {new["label"]!r})')
    return new['id'], new['label']


def main():
    print('=' * 70)
    print('Per-row Comments + Last Comment summary — idempotent setup')
    print('=' * 70)

    # 1. Flag dropdown on Comments
    print('\n[1] Flag dropdown on Comments')
    flag_fid = ensure_field(T_COMM, {
        'label': 'Flag',
        'fieldType': 'text-multiple-choice',
        'addToForms': True,
        'properties': {
            'choices': ['Investigating', 'Needs Action', 'In Progress',
                        'Resolved', 'Dismissed'],
            'allowNewChoices': False,
            'sortAsGiven': True,
        },
    })

    # 2. Helper formula: Sortable_Last_Note (kept for reporting/sorting use)
    print('\n[2] Sortable_Last_Note formula on Comments')
    sortable_formula = (
        'ToFormattedText([Date Created],"yyyy-MM-dd HH:mm")'
        ' & " - " & ToText([Record Owner])'
        ' & If(ToText([Flag])<>""," ["&ToText([Flag])&"]","")'
        ' & ": " & Left(ToText([Note]),200)'
    )
    ensure_field(T_COMM, {
        'label': 'Sortable_Last_Note',
        'fieldType': 'text',
        'appearsByDefault': False,
        'addToForms': False,
        'properties': {'formula': sortable_formula},
    })

    # 3a. Summary on Projections: Last Comment Date = MAX of Date Created (fid 1)
    print('\n[3a] Last Comment Date summary on Projections (MAX of Date Created)')
    last_date_fid = ensure_summary('Last Comment Date',
                                    summary_fid=1, accumulation_type='MAX')

    # 3b. Lookup that date back onto Comments
    print('\n[3b] Lookup Last Comment Date back onto Comments')
    lookup_fid, lookup_label = ensure_lookup_back(last_date_fid, 'Last Comment Date')

    # 3c. Conditional formula: outputs only on the most recent comment row
    print('\n[3c] Last_Note_Display formula on Comments (newest-row-only)')
    display_formula = (
        f'If([Date Created]=[{lookup_label}],'
        ' ToFormattedText([Date Created],"yyyy-MM-dd HH:mm")'
        ' & " - " & ToText([Record Owner])'
        ' & If(ToText([Flag])<>""," ["&ToText([Flag])&"]","")'
        ' & ": " & Left(ToText([Note]),200), "")'
    )
    last_note_fid = ensure_field(T_COMM, {
        'label': 'Last_Note_Display',
        'fieldType': 'text',
        'appearsByDefault': False,
        'addToForms': False,
        'properties': {'formula': display_formula},
    })

    # 3d. Final summary on Projections: Last Comment = COMBINED-TEXT of Last_Note_Display
    print('\n[3d] Last Comment summary on Projections (COMBINED-TEXT of Last_Note_Display)')
    last_comment_fid = ensure_summary('Last Comment',
                                       summary_fid=last_note_fid,
                                       accumulation_type='COMBINED-TEXT')

    # 4. Final verification
    print('\n[4] Verification')
    checks = [
        (T_COMM, 'Flag'),
        (T_COMM, 'Sortable_Last_Note'),
        (T_COMM, 'Last_Note_Display'),
        (T_PROJ, 'Last Comment Date'),
        (T_PROJ, 'Last Comment'),
        (T_PROJ, '# of Projection Comments'),
    ]
    all_ok = True
    for tbl, lbl in checks:
        f = find_field(tbl, lbl)
        mark = '+' if f else '-'
        fid = f['id'] if f else '--'
        print(f'  {mark} {tbl} :: {lbl:<32s} fid={fid}')
        if not f:
            all_ok = False

    print('\n' + ('DONE — all checks pass' if all_ok else 'INCOMPLETE — see above'))


if __name__ == '__main__':
    main()
