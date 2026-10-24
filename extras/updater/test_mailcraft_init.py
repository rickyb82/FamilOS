import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('shutdown',Path(__file__).parent/'orbita_support/prepare-shutdown.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class MailcraftInitTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.config=self.root/'compose.familos.yaml'
  self.config.write_text(json.dumps({'services':{'familos-mailcraft':{'image':'original'}}}))
  self.data=self.root/'data/mail';self.data.mkdir(parents=True);(self.data/'message').write_text('preserve')
  self.container={'State':{'Running':True},'Mounts':[{'RW':True,'Type':'bind','Source':str(self.data)}]}
 def run_migration(self,dc):
  with patch.object(module,'ROOT',self.root),patch.object(module,'dc',dc),patch.object(module.subprocess,'check_output',side_effect=['container',json.dumps([self.container]),json.dumps([{'State':{'Running':False,'ExitCode':137,'OOMKilled':False}}])]):
   module.prepare_mailcraft(self.root/'recovery')
 def test_migrate_preserves_data_and_enables_init(self):
  from unittest.mock import Mock
  dc=Mock();self.run_migration(dc)
  self.assertTrue(json.loads(self.config.read_text())['services']['familos-mailcraft']['init'])
  self.assertEqual(next((self.root/'recovery').glob('*/data/data/mail/message')).read_text(),'preserve')
  self.assertIn(('up','-d','--no-deps','--no-build','--pull','never','familos-mailcraft'),[c.args for c in dc.call_args_list])
 def test_failure_restores_config_and_restarts(self):
  from unittest.mock import Mock
  original=self.config.read_bytes();dc=Mock(side_effect=[None,RuntimeError('invalid compose'),None])
  with self.assertRaises(RuntimeError):self.run_migration(dc)
  self.assertEqual(self.config.read_bytes(),original);self.assertEqual(dc.call_args_list[-1].args[0],'up')
 def test_external_data_rejected_before_stop(self):
  from unittest.mock import Mock
  self.container['Mounts'][0]['Source']='/outside/data';dc=Mock()
  with self.assertRaises(RuntimeError):self.run_migration(dc)
  dc.assert_not_called()
 def test_already_configured_no_restart(self):
  self.config.write_text(json.dumps({'services':{'familos-mailcraft':{'init':True}}}))
  with patch.object(module,'ROOT',self.root),patch.object(module,'dc') as dc:
   module.prepare_mailcraft(self.root/'recovery');dc.assert_not_called()

if __name__=='__main__':unittest.main()
