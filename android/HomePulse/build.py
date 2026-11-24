#!/usr/bin/env python3
"""Reproducible Java/framework APK build, no Gradle plugins or third-party runtime."""
from pathlib import Path
import argparse,hashlib,json,os,secrets,shutil,subprocess,zipfile,xml.etree.ElementTree as ET
project=Path(__file__).resolve().parent
workspace=project.parents[3]
parser=argparse.ArgumentParser();parser.add_argument('--instrumentation',action='store_true');args=parser.parse_args()
sdk=Path(os.environ.get('HOME_PULSE_ANDROID_SDK',str(workspace/'work/android-sdk')))
jdk=Path(os.environ.get('HOME_PULSE_JDK',str(workspace/'work/oura-re/tools/temurin/jdk-21.0.13.1+2/Contents/Home')))
android=sdk/'platforms/android-36/android.jar';tools=sdk/'build-tools/36.0.0'
for p in (android,tools/'aapt2',tools/'lib/d8.jar',jdk/'bin/javac'):
 if not p.exists():raise SystemExit('Prerequisito mancante: '+str(p))
build=project/('.build/instrumentation' if args.instrumentation else '.build/apk')
build.mkdir(parents=True,exist_ok=True)
for name in ('classes','generated','dex'):
 path=build/name
 if path.exists():shutil.rmtree(path)
 path.mkdir()
private=workspace/'work/homepulse-android-private';private.mkdir(mode=0o700,exist_ok=True)
keystore=private/'homepulse-test.p12';password=private/'signing-password.txt'
def run(command):subprocess.run([str(v) for v in command],check=True)
if not password.exists():password.write_text(secrets.token_urlsafe(30));password.chmod(0o600)
if not keystore.exists():
 run([jdk/'bin/keytool','-genkeypair','-alias','homepulse','-keyalg','RSA','-keysize','3072','-validity','3650','-dname','CN=Home Pulse Test, OU=Home Pulse, O=Personal, C=IT','-keystore',keystore,'-storetype','PKCS12','-storepass:file',password,'-keypass:file',password,'-noprompt']);keystore.chmod(0o600)
manifest=project/'app/src/main/AndroidManifest.xml'
if args.instrumentation:
 tree=ET.parse(manifest);root=tree.getroot();root.append(ET.Element('instrumentation',{'{http://schemas.android.com/apk/res/android}name':'com.familos.homepulse.SelfTestInstrumentation','{http://schemas.android.com/apk/res/android}targetPackage':'com.familos.homepulse','{http://schemas.android.com/apk/res/android}label':'Home Pulse isolated tests'}));manifest=build/'AndroidManifest.xml';tree.write(manifest,encoding='utf-8',xml_declaration=True)
run([tools/'aapt2','compile','--dir',project/'app/src/main/res','-o',build/'resources.zip'])
run([tools/'aapt2','link','-o',build/'resources.apk','--manifest',manifest,'-I',android,'--java',build/'generated',build/'resources.zip'])
sources=sorted((project/'app/src/main/java').rglob('*.java'))+sorted((build/'generated').rglob('*.java'))
if args.instrumentation:sources+=sorted((project/'tests').rglob('*.java'))
sourceList=build/'sources.txt';sourceList.write_text('\n'.join(str(p) for p in sources)+'\n')
run([jdk/'bin/javac','--release','17','-encoding','UTF-8','-classpath',android,'-d',build/'classes','@'+str(sourceList)])
run([jdk/'bin/jar','--create','--file',build/'classes.jar','-C',build/'classes','.'])
run([jdk/'bin/java','-cp',tools/'lib/d8.jar','com.android.tools.r8.D8','--min-api','26','--lib',android,'--output',build/'dex',build/'classes.jar'])
unsigned=build/'unsigned.apk';shutil.copyfile(build/'resources.apk',unsigned)
with zipfile.ZipFile(unsigned,'a',compression=zipfile.ZIP_DEFLATED) as archive:
 for p in sorted((build/'dex').glob('classes*.dex')):archive.write(p,p.name)
run([tools/'zipalign','-f','4',unsigned,build/'aligned.apk'])
output=build/('HomePulse-tests.apk' if args.instrumentation else 'HomePulse-Fold5.apk')
run([jdk/'bin/java','-jar',tools/'lib/apksigner.jar','sign','--ks',keystore,'--ks-key-alias','homepulse','--ks-pass','file:'+str(password),'--out',output,build/'aligned.apk'])
run([jdk/'bin/java','-jar',tools/'lib/apksigner.jar','verify','--verbose',output]);run([tools/'zipalign','-c','4',output])
report={'apk':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'bytes':output.stat().st_size,'min_sdk':26,'target_sdk':36,'compile_sdk':36,'instrumentation':args.instrumentation,'third_party_runtime_dependencies':0,'contains_owner_credentials':False}
(build/'build-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if not args.instrumentation:
 shutil.copyfile(output,workspace/'outputs/HomePulse-Fold5.apk')
 (workspace/'outputs/HomePulse-Fold5-build.json').write_text(json.dumps(report,indent=2)+'\n')
