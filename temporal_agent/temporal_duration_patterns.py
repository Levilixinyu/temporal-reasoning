"""Extract declared duration definitions and policies with source evidence.

Unbound policy clauses retain an explicit scope limitation; they are never
silently applied to every timestamp or outcome in a question.
"""
import re

def duration_nodes(question):
    nodes=[]
    def add(m,kind,**fields):nodes.append(dict(type=kind,text=question[m.start():m.end()],span=list(m.span()),**fields))
    units=list(re.finditer(r'fractional\s+24-hour\s+days|fractional\s+days',question,re.I))
    day24=re.search(r'days are 24 hours|fractional\s+24-hour\s+days',question,re.I)
    unit='elapsed_day' if units and day24 else 'not inferred'
    for m in re.finditer(r'Measure each (hospital admission|ICU stay|prescribed interval) from (admittime|intime|starttime) to (dischtime|outtime|stoptime)',question,re.I):
        scope,start,end=m.groups()
        policies=[]
        invalid=re.search(r'Use non-null endpoints with end at or after start; retain zero duration',question,re.I)
        if scope.lower()=='prescribed interval':invalid=re.search(r'Exclude missing endpoints and stop times before start times; retain zero-duration records',question,re.I)
        clip=re.search(r'without clipping it to admission or discharge',question,re.I) if scope.lower()=='prescribed interval' else None
        weight=re.search(r'count every qualifying prescription row, including repeated rows, separately',question,re.I) if scope.lower()=='prescribed interval' else re.search(r'Count each (hadm_id|stay_id) once',question,re.I)
        for evidence in [invalid,clip,weight]:
            if evidence:policies.append(dict(text=evidence.group(),span=list(evidence.span())))
        add(m,'duration_definition',start=start,end=end,scope=scope.lower(),unit=unit,
            missing_endpoints='exclude' if invalid else 'not inferred',reversed_endpoints='exclude' if invalid else 'not inferred',
            zero_duration='retain' if invalid else 'not inferred',clip_to_encounter=False if clip else 'not inferred',
            weight='each prescription row including duplicates' if weight and scope.lower()=='prescribed interval' else 'each '+weight[1]+' once' if weight else 'not inferred',
            evidence=policies,unit_evidence=[dict(text=x.group(),span=list(x.span())) for x in units],resolution='explicit_fields_only')
    for m in re.finditer(r'\((dischtime|outtime|stoptime)\s*-\s*(admittime|intime|starttime)\)\s+in\s+fractional(?:\s+24-hour)?\s+days',question,re.I):
        add(m,'duration_definition',start=m[2],end=m[1],unit=unit,
            scope='Only the named endpoint expression',resolution='explicit_fields_only')
    for m in re.finditer(r'(?:Measure all LOS in fractional 24-hour days without truncation or rounding|LOS is exact elapsed fractional 24-hour days(?:, including zero except where the bins exclude it)?)',question,re.I):
        add(m,'duration_unit_policy',unit='elapsed_day',hours_per_day=24,truncate=False,
            zero_before_bins='retain' if 'including zero' in m.group().lower() else 'not inferred',scope='LOS; endpoint scope is supplied elsewhere in the question')
    for m in re.finditer(r'Restrict exact LOS to \[(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)\) days and label \[(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)\) as group_id \'([^\']+)\' and \[(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)\) as \'([^\']+)\'',question,re.I):
        a,b,c,d,label1,e,f,label2=m.groups()
        add(m,'duration_bins',unit='elapsed_day',eligibility=dict(lower=float(a),upper=float(b),lower_inclusive=True,upper_inclusive=False),
            bins=[dict(label=label1,lower=float(c),upper=float(d),lower_inclusive=True,upper_inclusive=False),dict(label=label2,lower=float(e),upper=float(f),lower_inclusive=True,upper_inclusive=False)])
    for m in re.finditer(r'Within that selected admission sum all linked ICU stay durations with non-null endpoints and outtime>=intime, retaining zero durations',question,re.I):
        overlap=re.search(r'Sum recorded stay durations even if intervals overlap',question,re.I)
        add(m,'duration_aggregation',start='intime',end='outtime',unit=unit,aggregation='sum',
            scope='All linked ICU stays within the already selected admission',missing_endpoints='exclude',reversed_endpoints='exclude',zero_duration='retain',
            merge_overlaps=False if overlap else 'not inferred',overlap_evidence=overlap.group() if overlap else None)
    return nodes
