import copy
import json
import unittest
from pathlib import Path
from pydantic import ValidationError
from manifest import Manifest

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((Path(__file__).parent / 'examples/app.json').read_text())

    def test_example(self):
        Manifest.model_validate(self.doc)

    def test_rejects_escape_hatches_and_invalid_references(self):
        mutations = [
            lambda d: d.update(privileged=True),
            lambda d: d['services']['web'].update(volumes=[{'name':'docs','mount':'/var/run/docker.sock','backup':'stop-and-copy'}]),
            lambda d: d['services']['web'].update(image='nginx:latest'),
            lambda d: d['services']['web'].update(uid=0),
            lambda d: d['services']['web'].update(port=True),
            lambda d: d['launch'].update(service='missing'),
            lambda d: d['launch'].update(path='/../admin'),
            lambda d: d.update(icon='../../secrets.png'),
            lambda d: d.update(sdk_version='2'),
            lambda d: d['services'].update(other=copy.deepcopy(d['services']['web'])),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                doc = copy.deepcopy(self.doc)
                mutate(doc)
                with self.assertRaises(ValidationError):
                    Manifest.model_validate(doc)

if __name__ == '__main__':
    unittest.main()
