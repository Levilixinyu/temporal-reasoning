"""Question-only temporal extraction, followed by conservative semantic normalization.

No reference SQL, execution outcomes, or clinical rubric is used. Unspecified
anchors, interval endpoints, and selection order remain explicit uncertainties.
This is a partial parser, not a certified representation of the whole question.
"""
from __future__ import annotations
import re
from .temporal import extract_time_expressions
from .temporal_question_patterns import explicit_nodes
from .temporal_event_patterns import semantic_nodes
from .temporal_duration_patterns import duration_nodes

VERSION='temporal-pipeline-v6-service-selection'
DASHES=str.maketrans({'–':'-','—':'-','‑':'-','−':'-'})


def analyze(question: str, reference_time: str | None=None) -> dict:
    q=question.translate(DASHES)
    nodes=semantic_nodes(q)+duration_nodes(q)
    semantic_spans=[n['span'] for n in nodes]
    nodes.extend(n for n in explicit_nodes(q,reference_time) if not any(n['span'][0]<end and n['span'][1]>start for start,end in semantic_spans))
    explicit_spans=[tuple(n['span']) for n in nodes]
    for index,n in enumerate(nodes,1):
        n['id']=f't{index}'
        n['text']=question[slice(*n['span'])]
    def add(match,kind,**fields):
        if any(match.start()<end and match.end()>start for start,end in explicit_spans):return
        nodes.append({'id':f't{len(nodes)+1}','type':kind,'text':question[match.start():match.end()],
                      'span':[match.start(),match.end()],**fields})
    # Event-local windows are not calendar-relative and do not require today's date.
    event_windows=r'\b(first|initial|final|last)[ -]+(\d+)\s*(hours?|h|days?|d)\b'
    event_spans=[]
    for m in re.finditer(event_windows,q,re.I):
        position,number,unit=m.groups();unit='hour' if unit.lower().startswith('h') else 'day'
        # Only bind an anchor when it is explicitly adjacent in the question.
        after=q[m.end():m.end()+55].lower()
        explicit=re.match(r'\s+(?:after|following|of|from)\s+(?:the\s+)?(icu (?:admission|stay)|hospital admission|admission|discharge)\b',after)
        anchor=explicit.group(1) if explicit else None
        if position.lower()=='last' and not explicit:
            # Unanchored 'last N days' is calendar-relative, not an encounter window.
            continue
        add(m,'event_window',position=position.lower(),amount=int(number),unit=unit,anchor=anchor)
        event_spans.append(m.span())
    # Capture the whole ordinal phrase before matching simple first/last terms.
    # Otherwise "3rd latest ICU stay" is incorrectly reduced to "latest ICU stay".
    # Legacy numeric "N-th" spelling is accepted for reproducible older inputs.
    order_spans=[]
    ordinal_words={'first':1,'second':2,'third':3,'fourth':4}
    ranked_event=r'\b(?P<ordinal>[1-9]\d*(?:-?st|-?nd|-?rd|-?th)|first|second|third|fourth)\s+(?P<direction>earliest|latest|most recent)\s+(?P<event>(?:hospital\s+)?admissions?|icu (?:stay|admission)s?|discharges?|diagnos(?:is|es)|lab tests?)\b'
    for m in re.finditer(ranked_event,q,re.I):
        ordinal=m.group('ordinal').lower()
        rank=ordinal_words[ordinal] if ordinal in ordinal_words else int(re.match(r'\d+',ordinal).group())
        add(m,'event_order',position=m.group('direction').lower(),event=m.group('event').lower(),rank=rank)
        order_spans.append(m.span())
    for m in re.finditer(r'\b(first|last|latest|earliest|most recent|second|third)[ -]+(icu (?:stay|admission)s?|admissions?|discharges?|diagnos(?:is|es)|lab tests?)\b',q,re.I):
        if not any(m.start()<end and m.end()>start for start,end in order_spans):
            add(m,'event_order',position=m.group(1).lower(),event=m.group(2).lower())
    for m in re.finditer(r'\b(?:los|length of stay)\s*(<=|>=|<|>|≤|≥)\s*(\d+(?:\.\d+)?)\s*(days?|hours?|d|h)\b',q,re.I):
        add(m,'duration_threshold',operator={'≤':'<=','≥':'>='}.get(m.group(1),m.group(1)),
            amount=float(m.group(2)),unit='hour' if m.group(3).lower().startswith('h') else 'day')
    for m in re.finditer(r'\b(?:icu\s+)?(?:los|length of stay)\b',q,re.I):
        add(m,'duration_measure',scope='icu_stay' if m.group().lower().startswith('icu') else 'unspecified')
    for m in re.finditer(r'\b(\d+)[ -]+day\s+(?:all[ -]+cause\s+)?readmission\b',q,re.I):
        add(m,'readmission_window',amount=int(m.group(1)),unit='day')
    # Extract absolute expressions independently, without a fake calendar reference.
    absolute=r'\b(?:since|until|before|after)\s+(?:\d{4}(?:-\d{2}(?:-\d{2})?)?|\d{1,2}/\d{1,2}/\d{4})\b'
    for m in re.finditer(absolute,q,re.I):
        expression=extract_time_expressions(m.group(),None)[0]
        add(m,'absolute_window',window=expression.window.to_dict(),policy=expression.policy)
    relative=r'\b(?:(?:since|until)\s+\d+\s+(?:hours?|days?|weeks?|months?|years?)\s+ago|(?:since|until)\s+(?:last|this)\s+(?:hours?|days?|weeks?|months?|years?)|(?:(?:in|during|over)\s+)?(?:the\s+)?(?:last|past|this)\s+(?:\d+\s+)?(?:hours?|days?|weeks?|months?|years?))\b'
    for m in re.finditer(relative,q,re.I):
        if any(m.start()<end and m.end()>start for start,end in event_spans):
            continue
        extracted=extract_time_expressions(m.group(),reference_time) if reference_time else []
        add(m,'relative_calendar',window=extracted[0].window.to_dict() if extracted else None,
            requires_reference=reference_time is None)
    templates=[{'id':n['id'],'type':n['type'],'text':n['text']} for n in nodes]
    plan=[];unresolved=[]
    for n in nodes:
        step=dict(n);t=n['type']
        if t=='explicit_event_interval' and n['resolution']!='resolved':
            unresolved.append({'id':n['id'],'issue':'Named interval offset is unresolved; do not assume a number or unit.'})
        elif t=='event_window':
            step.update(scope='same encounter as the anchor',
                relation='anchor_start <= event_time relative to anchor_start + offset' if n['position'] in ('first','initial') else 'event_time relative to anchor_end - offset and anchor_end',
                boundary_policy='Unspecified by this phrase; do not silently choose inclusivity')
            if n['anchor'] is None:unresolved.append({'id':n['id'],'issue':'Event anchor not explicit in the matched phrase; resolve from full question or flag ambiguity.'})
            unresolved.append({'id':n['id'],'issue':'Window endpoint inclusivity requires a declared policy.'})
        elif t in ('duration_definition','duration_unit_policy','duration_bins','duration_aggregation'):
            unresolved.append({'id':n['id'],'issue':'Only declared duration fields are normalized; retain the full-question scope, cohort and selection order.'})
        elif t in ('paired_event_windows','explicit_time_constraint','explicit_duration','explicit_duration_unit'):
            unresolved.append({'id':n['id'],'issue':'This is a partial extraction; other boundaries, anchors, clipping, selection order and missingness need full-question review.'})
        elif t=='absolute_window' and n.get('window') is None:
            unresolved.append({'id':n['id'],'issue':'Endpoint inclusivity is not explicit in the matched phrase.'})
        elif t=='event_order':
            step.update(direction='descending' if n['position'] in ('last','latest','most recent') else 'ascending',
                rank=n.get('rank',{'second':2,'third':3}.get(n['position'],1)),partition='not inferred',
                operation_order='Determine whether selection precedes cohort/time filters; not resolved by ordinal alone.',
                ties='not specified')
            unresolved.append({'id':n['id'],'issue':'Partition, ties, and order relative to filters need explicit resolution.'})
        elif t in ('duration_measure','duration_threshold'):
            step.update(start='icustays.intime' if n.get('scope')=='icu_stay' else 'encounter start (hospital or ICU scope must be determined)',
                end='icustays.outtime' if n.get('scope')=='icu_stay' else 'matching encounter end',
                semantics='Calendar-day difference versus exact elapsed duration is not specified by LOS alone.')
            if t=='duration_threshold':step['constraint']=f'end {n["operator"]} start + {n["amount"]:g} {n["unit"]}(s) IF exact elapsed duration is intended'
            unresolved.append({'id':n['id'],'issue':'Duration definition must be explicit; do not treat truncated calendar units as exact elapsed time.'})
        elif t=='readmission_window':
            step.update(index='index encounter discharge (definition must be specified)',
                candidate='subsequent admission for the same patient, different encounter',
                constraint=f'admission_time > index_discharge; upper limit {n["amount"]} days, inclusivity unresolved',
                order='Determine the next admission before applying eligibility filters if next-admission semantics are intended.')
            unresolved.append({'id':n['id'],'issue':'Index encounter, next versus any admission, and upper boundary are not fully specified.'})
        elif t=='relative_calendar' and n['requires_reference']:
            unresolved.append({'id':n['id'],'issue':'Explicit reference_time required; never use computer current date.'})
        plan.append(step)
    # Do not claim complete coverage: clinical temporality and unmatched phrases need review.
    return {'version':VERSION,'reference_time':reference_time,'templates':templates,'plan':plan,
            'unresolved':unresolved,'coverage':'partial_unvalidated' if nodes else 'no_supported_expression',
            'requires_reference':any(n.get('requires_reference',False) for n in nodes)}
