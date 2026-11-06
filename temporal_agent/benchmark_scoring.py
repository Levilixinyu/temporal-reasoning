"""Prospective result scoring for explicitly ordered benchmark outputs.

No SQL, question parser output or model explanation is used as a result oracle.
Temporal correctness and template alignment require separate adjudication.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation

VERSION='benchmark-result-comparator-v1'
FAILURES={'generation_failure','invalid_sql','budget_block','execution_failure'}

def numeric(value):
    if isinstance(value,bool) or not isinstance(value,(int,float,Decimal)):
        raise ValueError('Expected a numeric value, not a boolean or numeric string')
    result=Decimal(str(value))
    if not result.is_finite():raise ValueError('Non-finite numeric output')
    return result

def validate_config(config):
    columns=config['columns'];names=[c['name'] for c in columns]
    if not names or len(names)!=len(set(names)):raise ValueError('Column names must be unique')
    if config['row_order']!='ordered':raise ValueError('This version supports explicitly ordered outputs only')
    if config['column_order'] not in ('named','ordered'):raise ValueError('Unknown column-order policy')
    if 'rows' in config and (type(config['rows']) is not int or config['rows']<0):raise ValueError('Invalid row count')
    for col in columns:
        if col['kind'] not in ('integer','continuous','string'):raise ValueError('Unknown column kind')
        if type(col.get('nullable')) is not bool:raise ValueError('Column nullability must be declared')
        if col['kind']=='continuous':
            for key in ('abs_tolerance','rel_tolerance'):
                value=numeric(col[key])
                if value<0:raise ValueError('Negative tolerance')
    return names

def validate_rows(rows,columns):
    for row in rows:
        if len(row)!=len(columns):raise ValueError('Row width differs from declared columns')
        for value,col in zip(row,columns):
            if value is None:
                if not col['nullable']:raise ValueError('NULL in a non-nullable column')
            elif col['kind']=='string':
                if type(value) is not str:raise ValueError('Expected an exact string label')
            else:
                number=numeric(value)
                if col['kind']=='integer' and number!=number.to_integral_value():raise ValueError('Non-integral count or identifier')

def cells_match(a,b,col):
    if a is None or b is None:return a is None and b is None
    if col['kind']=='string':return a==b
    left,right=numeric(a),numeric(b)
    if col['kind']=='integer':return left==right
    tolerance=max(numeric(col['abs_tolerance']),numeric(col['rel_tolerance'])*max(abs(left),abs(right)))
    return abs(left-right)<=tolerance

def compare_result(predicted_columns,predicted_rows,reference_columns,reference_rows,config):
    """Compare complete results; never silently truncate, reorder rows or drop duplicates."""
    try:names=validate_config(config)
    except (KeyError,TypeError,ValueError,InvalidOperation) as exc:
        return dict(status='invalid_scoring_configuration',match=None,reason=str(exc))
    try:
        if reference_columns!=names:raise ValueError('Reference columns do not match the output contract')
        if 'rows' in config and len(reference_rows)!=config['rows']:raise ValueError('Reference row count does not match the output contract')
        validate_rows(reference_rows,config['columns'])
    except (TypeError,ValueError,InvalidOperation) as exc:
        return dict(status='invalid_reference',match=None,reason=str(exc))
    try:
        if len(predicted_columns)!=len(names) or len(set(predicted_columns))!=len(names) or set(predicted_columns)!=set(names):
            raise ValueError('Missing, extra or duplicate predicted column names')
        if config['column_order']=='ordered' and predicted_columns!=names:raise ValueError('Requested column order differs')
        if any(len(row)!=len(names) for row in predicted_rows):raise ValueError('Predicted row width differs')
        positions=[predicted_columns.index(name) for name in names]
        actual=[[row[i] for i in positions] for row in predicted_rows]
        validate_rows(actual,config['columns'])
        if len(actual)!=len(reference_rows):return dict(status='evaluated',match=False,reason='Different row count')
        for row_index,(got,want) in enumerate(zip(actual,reference_rows)):
            for col,a,b in zip(config['columns'],got,want):
                if not cells_match(a,b,col):
                    return dict(status='evaluated',match=False,reason='Cell mismatch',row_index=row_index,column=col['name'])
        return dict(status='evaluated',match=True,reason=None)
    except (TypeError,ValueError,InvalidOperation) as exc:
        return dict(status='invalid_prediction_output',match=False,reason=str(exc))

def score_axes(execution_result, *, temporal=None, template_alignment=None):
    """Missing independent temporal/alignment evidence stays unscored."""
    return dict(result_correctness=execution_result,
                temporal_correctness=temporal if temporal is not None else dict(status='not_evaluated',match=None),
                template_alignment=template_alignment if template_alignment is not None else dict(status='not_evaluated',match=None))

def summarize_first_pass(records,expected_ids):
    """Keep failed/budgeted predictions in the valid-reference denominator."""
    ids=[r['id'] for r in records]
    if len(ids)!=len(set(ids)) or set(ids)!=set(expected_ids) or len(expected_ids)!=len(set(expected_ids)):
        raise ValueError('All expected IDs must appear exactly once; missing runs cannot be silently dropped')
    allowed=FAILURES|{'evaluated','invalid_prediction_output','invalid_reference','invalid_scoring_configuration'}
    for row in records:
        if row['status'] not in allowed:raise ValueError('Unknown status')
        expected_match=(True,False) if row['status']=='evaluated' else (None,) if row['status'] in ('invalid_reference','invalid_scoring_configuration') else (False,)
        if not any(row.get('match') is value for value in expected_match):raise ValueError('Status and match are inconsistent')
    counts=Counter(r['status'] for r in records)
    denominator=sum(r['status'] not in ('invalid_reference','invalid_scoring_configuration') for r in records)
    correct=sum(r['match'] is True for r in records)
    return dict(total=len(records),status_counts=dict(counts),valid_reference_denominator=denominator,correct=correct,
                first_pass_accuracy=correct/denominator if denominator else None,
                reportable=not counts['invalid_scoring_configuration'],
                note='Invalid references are reported separately; budget, SQL, generation and execution failures remain in the denominator.')
