"""Question-only patterns for explicit timestamp, calendar and event expressions.

No benchmark IDs, reference answers, SQL or outcome evidence are inputs.
Unmatched semantics remain unresolved; finding a phrase is not full coverage.
"""
import re
from datetime import datetime, timedelta
from .temporal import timestamp, iso, floor, shift

STAMP = r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?!\d|\.\d)'
MONTH = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
NAMED_DATE = rf'{MONTH}\s+\d{{1,2}},\s*\d{{4}}'
FIELD = r'(?:admittime|dischtime|intime|outtime|charttime|starttime|stoptime|deathtime)'

def explicit_nodes(question, reference_time=None):
    nodes=[]; occupied=[]
    def add(m, kind, **fields):
        if any(m.start()<b and m.end()>a for a,b in occupied):return
        nodes.append(dict(type=kind,text=question[m.start():m.end()],span=[m.start(),m.end()],**fields))
        occupied.append(m.span())
    def window(start,end,lower=True,upper=False):
        return dict(start=iso(start) if start is not None else None,end=iso(end) if end is not None else None,
                    start_inclusive=lower,end_inclusive=upper)
    def named(value):return datetime.strptime(value, '%B %d, %Y')
    # Bind both endpoints before considering single-bound phrases.
    paired=rf'\b(at or after|strictly after|after)\s+({STAMP})\s+and\s+(at or before|strictly before|before)\s+({STAMP})'
    for m in re.finditer(paired,question,re.I):
        lo,a,hi,b=m.groups()
        add(m,'absolute_window',window=window(timestamp(a),timestamp(b),lo.lower()=='at or after',hi.lower()=='at or before'),policy='Explicit timestamp endpoints')
    for m in re.finditer(rf'\bbetween\s+({STAMP})\s+and\s+({STAMP})(?:,?\s+(including|excluding) both endpoints)?',question,re.I):
        a,b,bounds=m.groups()
        add(m,'absolute_window',window=window(timestamp(a),timestamp(b),bounds.lower()=='including',bounds.lower()=='including') if bounds else None,
            policy='Explicit timestamp endpoints' if bounds else 'Endpoint inclusivity unresolved')
    for m in re.finditer(rf'\bfrom\s+({NAMED_DATE})\s+through\s+({NAMED_DATE})',question,re.I):
        a,b=m.groups();add(m,'absolute_window',window=window(named(a),named(b)+timedelta(days=1)),policy='Whole dates, through includes the final date')
    for m in re.finditer(rf'\bon\s+({NAMED_DATE})',question,re.I):
        a=named(m.group(1));add(m,'absolute_window',window=window(a,a+timedelta(days=1)),policy='Whole named calendar date')
    for m in re.finditer(rf'\bin\s+({MONTH})\s+(\d{{4}})',question,re.I):
        a=datetime.strptime(' '.join(m.groups()),'%B %Y');add(m,'absolute_window',window=window(a,shift(a,1,'month')),policy='Whole named calendar month')
    for m in re.finditer(r'\bin\s+(?:the\s+)?(first|second|third|fourth|[1-4](?:st|nd|rd|th))\s+calendar quarter of\s+(\d{4})',question,re.I):
        rank={'first':1,'second':2,'third':3,'fourth':4}.get(m.group(1).lower()) or int(m.group(1)[0])
        a=datetime(int(m.group(2)),3*(rank-1)+1,1);add(m,'absolute_window',window=window(a,shift(a,3,'month')),policy='Whole named calendar quarter')
    for m in re.finditer(r'\bin\s+calendar year\s+(\d{4})',question,re.I):
        a=datetime(int(m.group(1)),1,1);add(m,'absolute_window',window=window(a,shift(a,1,'year')),policy='Whole named calendar year')
    for m in re.finditer(rf'\b(at or after|strictly after|after|at or before|strictly before|before)\s+({STAMP})',question,re.I):
        relation=m.group(1).lower();a=timestamp(m.group(2));lower='after' in relation
        add(m,'absolute_window',window=window(a if lower else None,None if lower else a,relation=='at or after',relation=='at or before'),policy='Exact timestamp, not a whole day')
    calendar=r'\b(previous complete calendar (?:month|quarter|week|year)|this calendar (?:month|quarter|week|year)|today|yesterday)\b'
    for m in re.finditer(calendar,question,re.I):
        # Include the attached boundary clause, preventing "this month" in the
        # end-bound description from becoming an unrelated second window.
        tail=question[m.end():];clause=re.match(r'[^?!.]*',tail).group()
        if clause and clause[0]==',':
            end=m.end()+len(clause);m=re.compile(re.escape(question[m.start():end])).search(question,m.start())
        phrase=m.group().lower();head=phrase.split(',')[0]
        to_reference='up to the reference time exclusive' in phrase
        unit='day' if head in ('today','yesterday') else head.split()[-1]
        previous=head.startswith('previous') or head=='yesterday'
        result=None
        if reference_time is not None:
            ref=timestamp(reference_time)
            base=ref.replace(month=((ref.month-1)//3)*3+1,day=1,hour=0,minute=0,second=0,microsecond=0) if unit=='quarter' else floor(ref,unit)
            amount=3 if unit=='quarter' else 1;shift_unit='month' if unit=='quarter' else unit
            start=shift(base,-amount,shift_unit) if previous else base
            end=base if previous else ref if to_reference else shift(base,amount,shift_unit)
            result=window(start,end)
        add(m,'relative_calendar',window=result,requires_reference=reference_time is None,
            policy='Previous complete calendar period' if previous else 'Calendar period to reference' if to_reference else 'Whole calendar period')
    # Shared trailing units: "first 24 and final 48 elapsed hours".
    for m in re.finditer(r'\b(first|initial)\s+(\d+)\s+and\s+(final|last)\s+(\d+)\s+(?:elapsed\s+)?(hours?|days?)\b',question,re.I):
        early,n,late,k,unit=m.groups()
        add(m,'paired_event_windows',early_amount=int(n),late_amount=int(k),unit=unit.lower().rstrip('s'),
            anchor='not inferred',boundary_policy='Resolve explicit interval notation or boundary clauses in the question')
    for m in re.finditer(r'\b(first|initial|final|last)\s+(\d+)\s+elapsed\s+(hours?|days?)(?:\s+(?:after|of|from)\s+(?:their associated\s+|the\s+)?(hospital admission|ICU intime|ICU admission|admission|discharge))?',question,re.I):
        position,n,unit,anchor=m.groups()
        if position.lower()=='last' and anchor is None:continue
        add(m,'event_window',position=position.lower(),amount=int(n),unit=unit.lower().rstrip('s'),anchor=anchor)
    # Preserve symbolic conditions without making up missing anchors or filters.
    for m in re.finditer(rf'\b{FIELD}\s*(?:<=|>=|<|>|=)\s*{FIELD}(?:\s*(?:<|<=|>|>=)\s*{FIELD})?(?:\s*[+-]\s*\d+\s+(?:elapsed\s+)?(?:hours?|days?))?',question,re.I):
        add(m,'explicit_time_constraint',expression=m.group(),semantics='Preserve the stated operators and fields; resolve associated encounter and other constraints from the full question')
    for m in re.finditer(rf'\bfrom\s+({FIELD})\s+to\s+({FIELD})\b',question,re.I):
        add(m,'explicit_duration',start=m.group(1),end=m.group(2),semantics='Duration endpoints explicitly named; units, missingness and clipping require full-question review')
    for m in re.finditer(r'\b(first|earliest|latest|last)\s+(?:(?:eligible|qualifying|non-null|numeric)\s+)*(hospital admission|ICU intime|ICU stay|numeric result|result)\b',question,re.I):
        if re.search(r'(?:\d+(?:-?st|-?nd|-?rd|-?th)|first|second|third|fourth)\s+$',question[:m.start()],re.I):continue
        add(m,'event_order',position=m.group(1).lower(),event=m.group(2).lower())
    for m in re.finditer(r'\b(?:hospital admission|ICU stay|individual prescription)\s+durations?\s+in\s+fractional\s+24-hour\s+days\b',question,re.I):
        add(m,'explicit_duration_unit',unit='day',hours_per_day=24,semantics='Fractional elapsed days; do not truncate to calendar days')
    return nodes
