"""Copy a privately supplied, already decrypted model into the signed device app.

No key, decryption routine, download or Oura service is involved in this build.
Research/ is excluded from source control and source deliveries.
"""
from pathlib import Path
import os
import shutil
import zipfile

source = Path(os.environ.get('HOME_PULSE_PRIVATE_MODEL', str(Path(__file__).parent/'Research/whr_2_5_1.pt')))
if not source.is_file() or not 1_000_000 < source.stat().st_size < 10_000_000:
    raise SystemExit('Supply your private WHR U2.5.1 model in Research/whr_2_5_1.pt')
with zipfile.ZipFile(source) as archive:
    if archive.testzip() is not None or 'UNetCombined_U2.5.1/data.pkl' not in archive.namelist():
        raise SystemExit('Private WHR U2.5.1 model integrity/identity check failed')
destination = Path(os.environ['TARGET_BUILD_DIR'])/os.environ['UNLOCALIZED_RESOURCES_FOLDER_PATH']/'whr_2_5_1.pt'
destination.parent.mkdir(parents=True,exist_ok=True)
shutil.copyfile(source,destination)
os.chmod(destination,0o600)
license_file=Path(__file__).parent/'Research/LibTorch/LICENSE'
if not license_file.is_file():raise SystemExit('Official PyTorch runtime license missing')
shutil.copyfile(license_file,destination.with_name('PyTorch-LICENSE.txt'))
print('Private offline cardio model copied; no key bundled.')
