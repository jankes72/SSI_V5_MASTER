import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ssi_v5.lifecycle.generation_registry import GenerationLifecycleRegistry
from ssi_v5.predictors.artifact import PredictorArtifactRegistry
from ssi_v5.predictions.registry import PredictionRegistry
from ssi_v5.outcomes.registry import OutcomeEvaluationRegistry, OutcomeEvaluationError, outcome_evaluation_status


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OutcomeEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        life = GenerationLifecycleRegistry(self.root/'lifecycle'/'generation_lifecycle.sqlite3')
        g = life.create_generation(world_id='WORLD__SPORT',domain_id='DOMAIN__TENNIS',network_id='NET__T',generation=1,governance_id='GOV1',governance_hash='abc')
        for state,kind,ref in [('QUEUED','GOVERNANCE','q'),('TRAINING','EXECUTION','t'),('HISTORICALLY_EVALUATED','HISTORICAL','h'),('SHADOW','HISTORICAL','h2')]:
            g=life.transition(g.generation_id,state,authority_type='WORLD_POLICY',authority_id='GOV1',reason='test',evidence_kind=kind,evidence_ref=ref)
        life.close(); self.gid=g.generation_id
        src=self.root/'src'; src.mkdir()
        for n,b in [('model.joblib',b'model'),('feature_schema.json',b'{}'),('metrics.json',b'{}')]: (src/n).write_bytes(b)
        manifest={'world_id':'WORLD__SPORT','domain_id':'DOMAIN__TENNIS','network_id':'NET__T','generation':1,'generation_id':self.gid,'governance_id':'GOV1','governance_hash':'abc','source_job_id':'JOB1','file_hashes':{n:sha(src/n) for n in ('model.joblib','feature_schema.json','metrics.json')}}
        (src/'predictor_manifest.json').write_text(json.dumps(manifest))
        ar=PredictorArtifactRegistry(self.root); art=ar.register_from_directory(src,generation_id=self.gid); ar.close()
        pr=PredictionRegistry(self.root); self.pred=pr.register(predictor_artifact_id=art.predictor_artifact_id,target_time='2026-08-16T12:00:00Z',prediction_kind='WIN_PROBABILITY',prediction={'home':0.6,'away':0.4}); pr.close()
        self.reg=OutcomeEvaluationRegistry(self.root)
    def tearDown(self): self.reg.close(); self.tmp.cleanup()
    def test_verified_outcome_registers_and_verifies(self):
        o=self.reg.register_outcome(prediction_id=self.pred.prediction_id,status='VERIFIED',outcome={'winner':'home'},evidence_ref='R1',evidence_source='OFFICIAL'); self.assertEqual(self.reg.verify_outcome(o.outcome_id)['status'],'VALID')
    def test_unknown_and_waiting_are_valid_states(self):
        a=self.reg.register_outcome(prediction_id=self.pred.prediction_id,status='UNKNOWN',outcome={},evidence_ref='R1',evidence_source='SRC'); b=self.reg.register_outcome(prediction_id=self.pred.prediction_id,status='WAITING_RESULT',outcome={},evidence_ref='R2',evidence_source='SRC'); self.assertEqual(a.status,'UNKNOWN'); self.assertEqual(b.status,'WAITING_RESULT')
    def test_verified_requires_payload(self):
        with self.assertRaises(OutcomeEvaluationError): self.reg.register_outcome(prediction_id=self.pred.prediction_id,status='VERIFIED',outcome={},evidence_ref='R',evidence_source='SRC')
    def test_unknown_prediction_rejected(self):
        with self.assertRaises(OutcomeEvaluationError): self.reg.register_outcome(prediction_id='PRED__NOPE',status='UNKNOWN',outcome={},evidence_ref='R',evidence_source='SRC')
    def test_conflicting_verified_evidence_is_preserved(self):
        first=self.reg.register_outcome(prediction_id=self.pred.prediction_id,status='VERIFIED',outcome={'winner':'home'},evidence_ref='R1',evidence_source='A'); second=self.reg.register_outcome(prediction_id=self.pred.prediction_id,status='VERIFIED',outcome={'winner':'away'},evidence_ref='R2',evidence_source='B'); self.assertEqual(first.status,'VERIFIED'); self.assertEqual(second.status,'CONFLICT')
    def test_only_verified_outcome_may_be_evaluated(self):
        o=self.reg.register_outcome(prediction_id=self.pred.prediction_id,status='WAITING_RESULT',outcome={},evidence_ref='R',evidence_source='SRC');
        with self.assertRaises(OutcomeEvaluationError): self.reg.evaluate(outcome_id=o.outcome_id,evaluator_id='E1',evaluation={'correct':True})
    def test_evaluation_registers_and_verifies(self):
        o=self.reg.register_outcome(prediction_id=self.pred.prediction_id,status='VERIFIED',outcome={'winner':'home'},evidence_ref='R',evidence_source='SRC'); e=self.reg.evaluate(outcome_id=o.outcome_id,evaluator_id='E1',evaluation={'correct':True,'score':1.0}); self.assertEqual(self.reg.verify_evaluation(e.evaluation_id)['status'],'VALID'); self.assertEqual(e.prediction_id,self.pred.prediction_id)
    def test_outcome_tamper_detected(self):
        o=self.reg.register_outcome(prediction_id=self.pred.prediction_id,status='VERIFIED',outcome={'winner':'home'},evidence_ref='R',evidence_source='SRC'); self.reg.conn.execute("UPDATE outcomes SET evidence_ref='tampered' WHERE outcome_id=?",(o.outcome_id,)); self.reg.conn.commit(); self.assertEqual(self.reg.verify_outcome(o.outcome_id)['status'],'INVALID')
    def test_prediction_table_is_not_modified_by_outcome(self):
        before=PredictionRegistry(self.root); p1=before.get(self.pred.prediction_id); before.close(); self.reg.register_outcome(prediction_id=self.pred.prediction_id,status='VERIFIED',outcome={'winner':'home'},evidence_ref='R',evidence_source='SRC'); after=PredictionRegistry(self.root); p2=after.get(self.pred.prediction_id); after.close(); self.assertEqual(p1.content_hash,p2.content_hash)
    def test_status_contract(self):
        s=outcome_evaluation_status(self.root); self.assertTrue(s['prediction_is_immutable']); self.assertTrue(s['conflicting_evidence_preserved']); self.assertEqual(s['conflicting_verified_outcome_becomes'],'CONFLICT')

if __name__ == '__main__': unittest.main()
