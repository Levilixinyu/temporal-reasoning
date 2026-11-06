"""Conservative lexical gate followed by authoritative BigQuery dry-run checks."""
import re


def tokens(sql):
    out=[];i=0
    while i<len(sql):
        if sql[i].isspace():i+=1;continue
        if sql.startswith('--',i) or sql[i]=='#':
            end=sql.find('\n',i);i=len(sql) if end<0 else end+1;continue
        if sql.startswith('/*',i):
            end=sql.find('*/',i+2)
            if end<0:raise ValueError('Unclosed SQL comment')
            i=end+2;continue
        ch=sql[i]
        if ch in ('\"',"'",'`'):
            mark=ch*3 if ch!='`' and sql.startswith(ch*3,i) else ch
            j=i+len(mark)
            while j<len(sql):
                if sql[j]=='\\':j+=2;continue
                if sql.startswith(mark,j):break
                j+=1
            else:raise ValueError('Unclosed SQL quote')
            out.append(('identifier' if ch=='`' else 'literal',sql[i+len(mark):j]))
            i=j+len(mark);continue
        m=re.match(r'[A-Za-z_][A-Za-z_0-9]*',sql[i:])
        if m:out.append(('word',m.group().upper()));i+=len(m.group());continue
        out.append(('symbol',ch));i+=1
    return out


def check_sql(sql):
    if not isinstance(sql,str) or not sql.strip():raise ValueError('Empty SQL')
    ts=tokens(sql)
    if not ts or ts[0] not in [('word','SELECT'),('word','WITH')]:raise ValueError('Only SELECT/WITH queries are accepted')
    semis=[i for i,t in enumerate(ts) if t==('symbol',';')]
    if semis and semis!=[len(ts)-1]:raise ValueError('Multiple statements are not allowed')
    for i,(kind,value) in enumerate(ts):
        if kind=='word' and value in {'EXTERNAL_QUERY','EXTERNAL_OBJECT_TRANSFORM','EXPORT','EXECUTE','CALL'}:
            raise ValueError('External or procedural SQL is not allowed')
        if i+1<len(ts) and ts[i+1]==('symbol','('):
            if kind=='identifier':raise ValueError('Quoted routine calls are not allowed')
            if i>0 and ts[i-1]==('symbol','.'):
                # SAFE builtin namespace is not a dataset routine; deeper paths are rejected.
                if i<2 or ts[i-2]!=('word','SAFE') or (i>=3 and ts[i-3]==('symbol','.')):
                    raise ValueError('Dataset or remote routine calls are not allowed')


def check_dry_run(job,allowed_tables):
    if job.statement_type!='SELECT':raise ValueError('BigQuery did not identify a single SELECT')
    for t in job.referenced_tables or []:
        name=f'{t.project}.{t.dataset_id}.{t.table_id}'
        if name not in allowed_tables:raise ValueError('Query references a table outside the supplied schema')
    data=job.to_api_repr().get('statistics',{}).get('query',{})
    if data.get('referencedRoutines'):raise ValueError('User-defined routines are not allowed')
