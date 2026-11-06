import unittest
from types import SimpleNamespace
from temporal_agent.bigquery_readonly import check_sql,check_dry_run

class ReadOnlyTests(unittest.TestCase):
    def test_legitimate_sql_quotes_and_comments(self):
        for sql in ["SELECT ';DROP TABLE x' AS s; -- harmless",'WITH x AS (SELECT 1) SELECT * FROM x',
            'SELECT SAFE.PARSE_DATE("%Y", "2000")','SELECT COUNT(*) FROM `p.d.t`',
            "SELECT REGEXP_CONTAINS(drug,r'\\bfoo\\b') FROM `p.d.t`"]:check_sql(sql)
    def test_no_scripts_or_remote_execution(self):
        for sql in ['SELECT 1; SELECT 2','SELECT 1; DROP TABLE x',
            'SELECT `p.d.fn`(1)','SELECT p.d.fn(1)','SELECT SAFE.p.d.fn(1)',
            "SELECT * FROM EXTERNAL_QUERY('c','SELECT 1')",'CALL x()', 'SELECT 1 /*']:
            with self.assertRaises(ValueError,msg=sql):check_sql(sql)
    def test_authoritative_table_and_statement_checks(self):
        job=SimpleNamespace(statement_type='SELECT',referenced_tables=[SimpleNamespace(project='p',dataset_id='d',table_id='t')],to_api_repr=lambda:{})
        check_dry_run(job,{'p.d.t'})
        with self.assertRaises(ValueError):check_dry_run(job,{'p.d.other'})
        job.statement_type='SCRIPT'
        with self.assertRaises(ValueError):check_dry_run(job,{'p.d.t'})

if __name__=='__main__':unittest.main()
