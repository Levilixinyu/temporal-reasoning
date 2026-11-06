"""Normalize explicitly stated event intervals and selection rules from text only.

The returned evidence spans are auditable question excerpts. Unstated partitions,
filters, event fields or policies are not filled from benchmark reference data.
"""
import re

def semantic_nodes(question):
    nodes=[]
    def add(match,kind,**fields):
        nodes.append(dict(type=kind,text=question[match.start():match.end()],span=list(match.span()),**fields))
    def field(name):return {'field':name}
    def offset(name,amount,unit):return dict(anchor=name,offset=amount,unit=unit)
    def window(lower,upper):return dict(lower=lower,upper=upper,lower_inclusive=True,upper_inclusive=False)
    pairs=list(re.finditer(r'\bfirst\s+(\d+)\s+and\s+final\s+(\d+)\s+(?:elapsed\s+)?(hours?|days?)\b',question,re.I))
    paired=pairs[0] if len(pairs)==1 else None
    early_late={'early':int(paired[1]),'late':int(paired[2])} if paired else {}
    # The complete bracket notation itself states clipping and both boundaries.
    pattern=r'\[(admittime|intime)\s*,\s*min\(\s*(dischtime|outtime)\s*,\s*\1\s*\+\s*(\d+|early)\s*(hours?|days?)\s*\)\s*\)'
    for m in re.finditer(pattern,question,re.I):
        start,end,amount,unit=m.groups();unit=unit.lower().rstrip('s')
        n=int(amount) if amount.isdigit() else early_late.get(amount.lower())
        if not amount.isdigit() and paired and paired[3].lower().rstrip('s')!=unit:n=None
        add(m,'explicit_event_interval',window=window(field(start),{'min':[field(end),offset(start,n,unit)]}),
            anchor=start,clip_at=end,amount=n,unit=unit,position='early',
            resolution='resolved' if n is not None else 'unresolved_named_offset',
            offset_evidence=paired.group() if not amount.isdigit() and paired else None)
    pattern=r'\[max\(\s*(admittime|intime)\s*,\s*(dischtime|outtime)\s*-\s*(\d+|late)\s*(hours?|days?)\s*\)\s*,\s*\2\s*\)'
    for m in re.finditer(pattern,question,re.I):
        start,end,amount,unit=m.groups();unit=unit.lower().rstrip('s')
        n=int(amount) if amount.isdigit() else early_late.get(amount.lower())
        if not amount.isdigit() and paired and paired[3].lower().rstrip('s')!=unit:n=None
        add(m,'explicit_event_interval',window=window({'max':[field(start),offset(end,-n if n is not None else None,unit)]},field(end)),
            anchor=end,clip_at=start,amount=n,unit=unit,position='late',
            resolution='resolved' if n is not None else 'unresolved_named_offset',
            offset_evidence=paired.group() if not amount.isdigit() and paired else None)
    pattern=r'first\s+(\d+)\s+elapsed hours after ICU intime, clipped at outtime:\s*include charttime >= intime and exclude charttime >= either outtime or intime \+ \1 hours'
    for m in re.finditer(pattern,question,re.I):
        n=int(m[1]);add(m,'explicit_event_interval',event_field='charttime',anchor='intime',clip_at='outtime',amount=n,unit='hour',position='early',resolution='resolved',window=window(field('intime'),{'min':[field('outtime'),offset('intime',n,'hour')]}))
    pattern=r'admittime\s*<=\s*charttime\s*<\s*dischtime'
    for m in re.finditer(pattern,question,re.I):
        extra=re.search(r'Also require charttime < admittime \+ (\d+) elapsed hours',question,re.I)
        n=int(extra[1]) if extra else None
        upper={'min':[field('dischtime'),offset('admittime',n,'hour')]} if extra else field('dischtime')
        add(m,'explicit_event_interval',event_field='charttime',anchor='admittime',clip_at='dischtime',amount=n,unit='hour' if extra else None,position='early' if extra else 'encounter',resolution='resolved',window=window(field('admittime'),upper),offset_evidence=extra.group() if extra else None)
    # Explicit ordinal statement plus adjacent ordering/null-policy sentences.
    ordinal=r'For each patient, return the (\d+)(?:st|nd|rd|th) (earliest|latest) (hospital admission|ICU stay) by (admittime|intime)\. Order by \4 (ascending|descending), breaking tied timestamps by (hadm_id|stay_id) ascending\. Ignore rows with null \4'
    for m in re.finditer(ordinal,question,re.I):
        rank,which,event,clock,direction,key=m.groups()
        add(m,'explicit_selection',partition=['subject_id'],rank=int(rank),event=event,
            order_by=[dict(field=clock,direction=direction.lower()),dict(field=key,direction='ascending')],
            filters_before_selection=['Exclude null '+clock],filters_after_selection=[],
            end_time_filter='none' if 'Do not filter by discharge or end time.' in question else 'not inferred',
            resolution='resolved_for_declared_selection')
    pattern=r'Require the earliest services record with non-null transfertime and curr_service to have curr_service=([A-Z0-9_]+); order by transfertime then curr_service lexicographically'
    for m in re.finditer(pattern,question):
        linked=bool(re.search(r'Match all tables on subject_id and hadm_id',question,re.I))
        add(m,'explicit_selection',partition=['subject_id','hadm_id'] if linked else 'not inferred',
            rank=1,event='services record',order_by=[dict(field='transfertime',direction='ascending'),dict(field='curr_service',direction='ascending')],
            filters_before_selection=['Non-null transfertime and curr_service'],
            filters_after_selection=[dict(field='curr_service',operator='=',value=m[1])],
            no_substitution=True,resolution='resolved_for_declared_selection' if linked else 'partial_partition_unresolved')
    pattern=r'Select the first eligible numeric result per admission using charttime then labevent_id ascending, after the stated eligibility filters'
    for m in re.finditer(pattern,question,re.I):
        post=re.search(r'Only after selecting the first eligible result, retain admissions whose selected value is strictly greater than (\d+(?:\.\d+)?)\s*([^.?]+)',question,re.I)
        add(m,'explicit_selection',partition=['subject_id','hadm_id'],rank=1,event='eligible numeric laboratory result',
            order_by=[dict(field='charttime',direction='ascending'),dict(field='labevent_id',direction='ascending')],
            filters_before_selection=['All stated result eligibility filters, including numeric validity and time bounds'],
            filters_after_selection=[dict(field='selected_value',operator='>',value=float(post[1]),unit=post[2].strip())] if post else [],
            post_filter_evidence=post.group() if post else None,resolution='resolved_for_declared_selection')
    pattern=r'Use the earliest eligible charttime per stay; if tied, select the smallest eligible converted value at that time'
    for m in re.finditer(pattern,question,re.I):
        add(m,'explicit_selection',partition=['stay_id'],rank=1,event='eligible converted measurement',
            order_by=[dict(field='charttime',direction='ascending'),dict(field='converted_value',direction='ascending')],
            filters_before_selection=['All stated measurement eligibility filters'],filters_after_selection=[],
            exact_duplicate_policy='Same selected value; no unique record identifier inferred',resolution='resolved_for_declared_selection')
    pattern=r'Select the earliest non-null intime ICU stay per hospital admission, ordering by intime then stay_id, before checking outtime; never replace a first stay with missing/invalid outtime by a later one'
    for m in re.finditer(pattern,question,re.I):
        add(m,'explicit_selection',partition=['subject_id','hadm_id'],rank=1,event='ICU stay',
            order_by=[dict(field='intime',direction='ascending'),dict(field='stay_id',direction='ascending')],
            filters_before_selection=['Non-null intime'],filters_after_selection=['Validate outtime after selecting first stay'],
            no_substitution=True,resolution='resolved_for_declared_selection')
    pattern=r'For each primitive type, pool all its qualifying prescriptions and use only the earliest start within the admission for both windows; do not replace it with a later start'
    for m in re.finditer(pattern,question,re.I):
        add(m,'explicit_selection',partition=['subject_id','hadm_id','primitive_type'],rank=1,event='prescription start',
            order_by=[dict(field='starttime',direction='ascending')],
            filters_before_selection=['Prescription eligibility and primitive-type classification'],
            filters_after_selection=['Test the same earliest eligible start against both windows independently'],
            no_substitution=True,ties='Only the earliest timestamp is needed for window membership',resolution='resolved_for_declared_selection')
    pattern=r"For each patient, first select their earliest hospital admission among records with non-null admittime, breaking ties by hadm_id ascending\. Then retain that selected admission only if discharge occurred strictly more than (\d+) elapsed hours after admission"
    for m in re.finditer(pattern,question,re.I):
        add(m,'explicit_selection',partition=['subject_id'],rank=1,event='hospital admission',
            order_by=[dict(field='admittime',direction='ascending'),dict(field='hadm_id',direction='ascending')],
            filters_before_selection=['Non-null admittime'],
            filters_after_selection=[dict(duration_start='admittime',duration_end='dischtime',operator='>',amount=int(m[1]),unit='hour')],
            no_substitution=True,resolution='resolved_for_declared_selection')
    pattern=r'For each patient select the first qualifying hospital admission by admittime then hadm_id, before outcome/duration missingness checks; never substitute a later admission'
    for m in re.finditer(pattern,question,re.I):
        add(m,'explicit_selection',partition=['subject_id'],rank=1,event='qualifying hospital admission',
            order_by=[dict(field='admittime',direction='ascending'),dict(field='hadm_id',direction='ascending')],
            filters_before_selection=['Stated admission cohort and non-null ordering time'],
            filters_after_selection=['Outcome and duration missingness checks'],no_substitution=True,resolution='resolved_for_declared_selection')
    pattern=r'For each patient select the earliest non-null ICU intime across all qualifying admissions, ordering by intime then stay_id\. After selecting, exclude missing outtime or outtime<intime; never substitute a later stay'
    for m in re.finditer(pattern,question,re.I):
        add(m,'explicit_selection',partition=['subject_id'],rank=1,event='ICU stay across qualifying admissions',
            order_by=[dict(field='intime',direction='ascending'),dict(field='stay_id',direction='ascending')],
            filters_before_selection=['Stated qualifying admission cohort and non-null intime'],
            filters_after_selection=['Exclude null outtime and outtime < intime'],no_substitution=True,resolution='resolved_for_declared_selection')
    pattern=r"Select each patient's first code-qualifying admission with non-null admittime, ordering by admittime then hadm_id, before applying discharge/survival eligibility; never substitute a later admission"
    for m in re.finditer(pattern,question,re.I):
        add(m,'explicit_selection',partition=['subject_id'],rank=1,event='code-qualifying admission',
            order_by=[dict(field='admittime',direction='ascending'),dict(field='hadm_id',direction='ascending')],
            filters_before_selection=['Code qualification and non-null admittime'],filters_after_selection=['Discharge and survival eligibility'],
            no_substitution=True,resolution='resolved_for_declared_selection')
    pattern=r'The event is the earliest non-null admittime strictly after index dischtime; overlapping and exactly simultaneous admissions do not qualify, and must not hide a later eligible event\. Count the event if it is at or before dischtime \+ (\d+)\*24 hours'
    for m in re.finditer(pattern,question,re.I):
        same=re.search(r'Search every other admissions row for the same subject, regardless of diagnosis, age, insurance, discharge completeness or survival at that later admission',question,re.I)
        add(m,'explicit_readmission',index_anchor='dischtime',event_field='admittime',lower_inclusive=False,upper_inclusive=True,
            followup_days=int(m[1]),candidate_scope='Other admissions for the same subject' if same else 'not inferred',
            operation_order=['Exclude candidate times <= index discharge','Select earliest remaining non-null admission time','Compare selected time to inclusive follow-up endpoint'],
            candidate_cohort_filters='none from index cohort' if same else 'not inferred',scope_evidence=same.group() if same else None,
            resolution='resolved_for_declared_followup' if same else 'partial_candidate_scope_unresolved')
    pattern=r'For each primitive class, (?:use only its earliest eligible start|pool prescriptions of all its drugs and use the single earliest eligible start) within the admission for both windows'
    for m in re.finditer(pattern,question,re.I):
        add(m,'explicit_selection',partition=['subject_id','hadm_id','primitive_class'],rank=1,event='prescription start',
            order_by=[dict(field='starttime',direction='ascending')],filters_before_selection=['Prescription eligibility and class membership'],
            filters_after_selection=['Test the same earliest eligible start against both windows independently'],no_substitution=True,
            ties='Only the earliest timestamp is needed for window membership',resolution='resolved_for_declared_selection')
    pattern=r'first (\d+) elapsed hours after their associated hospital admission, including the admission timestamp and excluding the \1-hour endpoint'
    for m in re.finditer(pattern,question,re.I):
        clip=re.search(r'require the ICU start to be at or after admission and before discharge',question,re.I)
        n=int(m[1]);upper=offset('admittime',n,'hour')
        if clip:upper={'min':[field('dischtime'),upper]}
        add(m,'explicit_event_interval',event_field='intime',anchor='admittime',clip_at='dischtime' if clip else None,amount=n,unit='hour',position='early',
            resolution='resolved',window=window(field('admittime'),upper),clipping_evidence=clip.group() if clip else None)
    return nodes
