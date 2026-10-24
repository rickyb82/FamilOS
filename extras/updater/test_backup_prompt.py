import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('backup',Path(__file__).parent/'orbita_support/backup-fixed.py')
backup=importlib.util.module_from_spec(spec);spec.loader.exec_module(backup)
class BackupPromptTests(unittest.TestCase):
 def test_secret_uses_stdin_and_not_command_line(self):
  with patch.object(backup.sys.stdin,'isatty',return_value=True),patch.object(backup.getpass,'getpass',side_effect=['fixture-secret','fixture-secret']),patch.object(backup.subprocess,'run') as run:
   backup.encrypt_interactive(Path('archive'),Path('encrypted'))
   args,kw=run.call_args
   self.assertNotIn('fixture-secret',' '.join(args[0]))
   self.assertEqual(kw['input'],'fixture-secret\n')
   self.assertIn('--passphrase-fd',args[0]);self.assertTrue(kw['check'])
 def test_mismatch_does_not_encrypt(self):
  with patch.object(backup.sys.stdin,'isatty',return_value=True),patch.object(backup.getpass,'getpass',side_effect=['one','two']),patch.object(backup.subprocess,'run') as run:
   with self.assertRaises(RuntimeError):backup.encrypt_interactive('a','b')
   run.assert_not_called()
 def test_no_terminal_does_not_read_or_encrypt(self):
  with patch.object(backup.sys.stdin,'isatty',return_value=False),patch.object(backup.getpass,'getpass') as prompt:
   with self.assertRaises(RuntimeError):backup.encrypt_interactive('a','b')
   prompt.assert_not_called()
