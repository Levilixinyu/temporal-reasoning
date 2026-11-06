"""Resume must preserve old attempts while stopping on newly observed account failures."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH=Path(__file__).resolve().parents[1]/'benchmarks/temporal_balanced_v1/run_paired.py'
spec=importlib.util.spec_from_file_location('paired_runner',PATH)
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

class ResumeTests(unittest.TestCase):
    def exercise(self,new_error=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);freeze=root/'inputs';run=root/'generation';freeze.mkdir();run.mkdir()
            (freeze/'run_paired.py').write_bytes(PATH.read_bytes())
            def save(name,x): (freeze/name).write_text(json.dumps(x))
            save('manifest.json',dict(status='frozen',answer_generation_allowed=True))
            save('model_configuration.json',dict(model='test',reasoning_effort='medium',max_completion_tokens=10,system_message='test',generation_workers=4))
            schedule=[dict(order=i,number=(i-1)//4+1,id=str((i-1)//4),condition='ABCD'[(i-1)%4]) for i in range(1,13)]
            (freeze/'schedule.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in schedule))
            prompts=[dict(id=r['id'],condition=r['condition'],prompt={'question':'test'},prompt_sha256='test') for r in schedule]
            (freeze/'prompt_matrix.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in prompts))
            save('artifact_hashes.json',{p.name:runner.sha(p) for p in freeze.iterdir()})
            for task in schedule[:4]:
                (run/f"{task['order']:04d}.json").write_text(json.dumps(dict(**task,status='generation_failure',http_status=429)))
            old={p.name:p.read_bytes() for p in run.iterdir()}
            calls=[]
            class Client:
                def __enter__(self):return self
                def __exit__(self,*a):pass
                @property
                def chat(self):return self
                @property
                def completions(self):return self
                def create(self,**kw):
                    calls.append(kw)
                    if new_error:
                        e=RuntimeError('quota');e.status_code=429;raise e
                    return SimpleNamespace(model_dump=lambda **kw:{},id='test',model='test',usage=None,
                        choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='{"sql":"SELECT 1","explanation":"test"}'))])
            with patch.object(runner,'FREEZE',freeze),patch.object(runner,'RUN',run),patch.object(runner,'client',Client),contextlib.redirect_stdout(io.StringIO()):
                runner.run()
                for name,data in old.items():self.assertEqual((run/name).read_bytes(),data)
                if new_error:
                    self.assertEqual(len(calls),4);self.assertFalse((run/'0009.json').exists())
                else:
                    self.assertEqual(len(calls),8)
                    runner.run();self.assertEqual(len(calls),8)
                self.assertFalse((run/'RUNNING.lock').exists())

    def test_old_failures_are_not_retried_or_retriggered(self):self.exercise()
    def test_new_quota_failure_still_stops_next_batch(self):self.exercise(True)

if __name__=='__main__':unittest.main()
