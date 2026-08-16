import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ssi_v5.lifecycle.generation_registry import GenerationLifecycleRegistry
from ssi_v5.predictors.artifact import PredictorArtifactRegistry
from ssi_v5.predictions.registry import PredictionRegistry, PredictionRegistryError, prediction_registry_status


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PredictionRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        life = GenerationLifecycleRegistry(self.root / 'lifecycle' / 'generation_lifecycle.sqlite3')
        g = life.create_generation(world_id='WORLD__SPORT', domain_id='DOMAIN__TENNIS', network_id='NET__T', generation=1, governance_id='GOV1', governance_hash='abc')
        for state, kind, ref in [
            ('QUEUED','GOVERNANCE','q'), ('TRAINING','EXECUTION','t'), ('HISTORICALLY_EVALUATED','HISTORICAL','h'), ('SHADOW','HISTORICAL','h2')
        ]:
            g = life.transition(g.generation_id, state, authority_type='WORLD_POLICY', authority_id='GOV1', reason='test', evidence_kind=kind, evidence_ref=ref)
        self.generation_id = g.generation_id
        life.close()

        src = self.root / 'src'; src.mkdir()
        (src/'model.joblib').write_bytes(b'model')
        (src/'feature_schema.json').write_text('{}')
        (src/'metrics.json').write_text('{}')
        manifest = {
            'world_id':'WORLD__SPORT','domain_id':'DOMAIN__TENNIS','network_id':'NET__T','generation':1,
            'generation_id':self.generation_id,'governance_id':'GOV1','governance_hash':'abc','source_job_id':'JOB1',
            'file_hashes': {n: sha(src/n) for n in ('model.joblib','feature_schema.json','metrics.json')}
        }
        (src/'predictor_manifest.json').write_text(json.dumps(manifest))
        ar = PredictorArtifactRegistry(self.root)
        self.art = ar.register_from_directory(src, generation_id=self.generation_id)
        ar.close()
        self.reg = PredictionRegistry(self.root)

    def tearDown(self):
        self.reg.close(); self.tmp.cleanup()

    def make(self, **kw):
        d=dict(predictor_artifact_id=self.art.predictor_artifact_id,target_time='2026-08-16T12:00:00Z',prediction_kind='WIN_PROBABILITY',prediction={'home':0.6,'away':0.4},context={'x':1})
        d.update(kw); return self.reg.register(**d)

    def test_register_and_verify(self):
        p=self.make(); self.assertEqual(self.reg.verify(p.prediction_id)['status'],'VALID')
    def test_target_time_required(self):
        with self.assertRaises(PredictionRegistryError): self.make(target_time='')
    def test_prediction_payload_required(self):
        with self.assertRaises(PredictionRegistryError): self.make(prediction={})
    def test_duplicate_prediction_id_rejected(self):
        self.make(prediction_id='PRED__FIXED')
        with self.assertRaises(PredictionRegistryError): self.make(prediction_id='PRED__FIXED')
    def test_tamper_is_detected(self):
        p=self.make(); self.reg.conn.execute("UPDATE predictions SET target_time='tampered' WHERE prediction_id=?",(p.prediction_id,)); self.reg.conn.commit(); self.assertEqual(self.reg.verify(p.prediction_id)['status'],'INVALID')
    def test_invalid_predictor_artifact_rejected(self):
        Path(self.art.storage_path,'model.joblib').write_bytes(b'tamper')
        with self.assertRaises(PredictionRegistryError): self.make()
    def test_suspended_generation_cannot_predict(self):
        life=GenerationLifecycleRegistry(self.root/'lifecycle'/'generation_lifecycle.sqlite3'); life.transition(self.generation_id,'SUSPENDED',authority_type='WORLD_POLICY',authority_id='GOV1',reason='stop',evidence_kind='PROSPECTIVE',evidence_ref='e'); life.close()
        with self.assertRaises(PredictionRegistryError): self.make()
    def test_outcome_not_in_schema(self):
        cols=[r[1] for r in self.reg.conn.execute('PRAGMA table_info(predictions)').fetchall()]; self.assertNotIn('outcome', cols); self.assertNotIn('outcome_json', cols)
    def test_lineage_copied_from_artifact(self):
        p=self.make(); self.assertEqual(p.generation_id,self.generation_id); self.assertEqual(p.network_id,'NET__T'); self.assertEqual(p.governance_id,'GOV1')
    def test_status_contract(self):
        s=prediction_registry_status(self.root); self.assertTrue(s['immutable_predictions']); self.assertFalse(s['outcome_stored_in_prediction_record'])

if __name__ == '__main__': unittest.main()
