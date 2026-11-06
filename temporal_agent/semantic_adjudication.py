"""Validate evidence-backed semantic decisions; never infer them from result equality.

This module aggregates an adjudicator's decisions. It does not judge SQL semantics.
The SQL, rubric and exact question must be bound before a review can be accepted.
"""
import hashlib
import json

VERSION = 'semantic-adjudication-v1'
AXES = ('temporal_correctness', 'template_alignment')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def rubric_digest(rubric):
    return digest(json.dumps(rubric, sort_keys=True, ensure_ascii=True))


def aggregate(rubric, sql, review):
    """Reject stale/incomplete reviews; unresolved decisions can never become passes."""
    try:
        if rubric['axis'] not in AXES:
            raise ValueError('Unknown semantic axis')
        if review['axis'] != rubric['axis']:
            raise ValueError('Cross-axis evidence is not interchangeable')
        if review['rubric_sha256'] != rubric_digest(rubric) or review['sql_sha256'] != digest(sql):
            raise ValueError('Stale SQL or rubric evidence')
        if review['question_sha256'] != rubric['question_sha256']:
            raise ValueError('Stale question evidence')
        reviewer = review['reviewer']
        if not isinstance(reviewer['id'], str) or not reviewer['id'].strip():
            raise ValueError('Reviewer identity is required')
        if reviewer['method'] not in ('human_review', 'external_adjudication', 'developer_calibration'):
            raise ValueError('Unknown adjudication provenance')
        leaves = {r['id']: r for r in rubric['leaves']}
        if not leaves or len(leaves) != len(rubric['leaves']):
            raise ValueError('Empty or duplicate rubric leaves')
        decisions = review['decisions']
        if len(decisions) != len(leaves) or {d['leaf_id'] for d in decisions} != set(leaves):
            raise ValueError('Every required leaf needs exactly one decision')
        for d in decisions:
            if d['decision'] not in ('pass', 'fail', 'unresolved'):
                raise ValueError('Only pass/fail/unresolved decisions are permitted')
            if not isinstance(d['rationale'], str) or not d['rationale'].strip():
                raise ValueError('Decision rationale is required')
            if d['decision'] != 'unresolved' and not d['sql_evidence']:
                raise ValueError('Resolved decisions require SQL evidence, including for an absent constraint')
            for witness in d['sql_evidence']:
                start, end = witness['span']
                if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(sql):
                    raise ValueError('Invalid SQL evidence span')
                if sql[start:end] != witness['text']:
                    raise ValueError('SQL evidence is not verbatim')
        values = [d['decision'] for d in decisions]
        failed = values.count('fail'); unresolved = values.count('unresolved')
        # A witnessed failure disproves the conjunction even with other unresolved leaves.
        match = False if failed else None if unresolved else True
        ready = rubric.get('coverage_status') == 'reviewed' and reviewer['method'] != 'developer_calibration'
        return dict(status='adjudicated' if ready else 'provisional', match=match,
            reportable=ready and match is not None, passed=values.count('pass'), failed=failed,
            unresolved=unresolved, total=len(values), axis=rubric['axis'], scorer_version=VERSION,
            limitation='Checks evidence integrity and conjunction only; semantic accuracy depends on adjudication quality.')
    except (KeyError, TypeError, ValueError) as exc:
        return dict(status='invalid_adjudication', match=None, reportable=False, reason=str(exc))


def blinded_packet(question, sql, rubric):
    """Explicit allow-list: exclude arm, explanation, outputs, gold and fixture answers."""
    if digest(question['question']) != rubric['question_sha256']:
        raise ValueError('Question does not match rubric')
    return dict(question=question['question'], reference_time=question.get('reference_time'),
        sql=sql, sql_sha256=digest(sql), rubric=rubric, rubric_sha256=rubric_digest(rubric))
