from pathlib import Path
import hashlib,json
root=Path(__file__).resolve().parent
objects={}
def ident(s):return hashlib.sha1(s.encode()).hexdigest()[:24].upper()
def obj(name,body):
 i=ident(name);objects[i]=body;return i
def quoted(s):return json.dumps(str(s),ensure_ascii=False)
def arr(values):return '('+','.join(values)+',)'
files=list((root/'HomePulse').glob('*.swift'));tests=list((root/'HomePulseTests').glob('*.swift'));uitests=list((root/'HomePulseUITests').glob('*.swift'))
files+=list((root/'HomePulse').glob('*.mm'))
sourceRefs=[];sourceBuild=[];testRefs=[];testBuild=[];uiRefs=[];uiBuild=[]
for group,items,refs,builds in [('app',files,sourceRefs,sourceBuild),('test',tests,testRefs,testBuild),('ui',uitests,uiRefs,uiBuild)]:
 for f in items:
  kind='sourcecode.cpp.objcpp' if f.suffix=='.mm' else 'sourcecode.swift'
  ref=obj(group+f.name,'isa=PBXFileReference; lastKnownFileType='+kind+'; path='+quoted(f.name)+'; sourceTree="<group>";')
  refs.append(ref);builds.append(obj('build'+group+f.name,'isa=PBXBuildFile; fileRef='+ref+';'))
info=obj('info','isa=PBXFileReference; lastKnownFileType=text.plist.xml; path=Info.plist; sourceTree="<group>";');sourceRefs.append(info)
asset=obj('assets','isa=PBXFileReference; lastKnownFileType=folder.assetcatalog; path=Assets.xcassets; sourceTree="<group>";');sourceRefs.append(asset)
assetBuild=obj('assetBuild','isa=PBXBuildFile; fileRef='+asset+';')
appProduct=obj('appProduct','isa=PBXFileReference; explicitFileType=wrapper.application; path=HomePulse.app; sourceTree=BUILT_PRODUCTS_DIR;')
testProduct=obj('testProduct','isa=PBXFileReference; explicitFileType=wrapper.cfbundle; path=HomePulseTests.xctest; sourceTree=BUILT_PRODUCTS_DIR;')
uiProduct=obj('uiProduct','isa=PBXFileReference; explicitFileType=wrapper.cfbundle; path=HomePulseUITests.xctest; sourceTree=BUILT_PRODUCTS_DIR;')
uiSources=obj('uiSources','isa=PBXSourcesBuildPhase; buildActionMask=2147483647; files='+arr(uiBuild)+'; runOnlyForDeploymentPostprocessing=0;')
appSources=obj('appSources','isa=PBXSourcesBuildPhase; buildActionMask=2147483647; files='+arr(sourceBuild)+'; runOnlyForDeploymentPostprocessing=0;')
testSources=obj('testSources','isa=PBXSourcesBuildPhase; buildActionMask=2147483647; files='+arr(testBuild)+'; runOnlyForDeploymentPostprocessing=0;')
frameworks=obj('frameworks','isa=PBXFrameworksBuildPhase; buildActionMask=2147483647; files=(); runOnlyForDeploymentPostprocessing=0;')
resources=obj('resources','isa=PBXResourcesBuildPhase; buildActionMask=2147483647; files='+arr([assetBuild])+'; runOnlyForDeploymentPostprocessing=0;')
modelScript='if [ "$PLATFORM_NAME" = "iphoneos" ]; then\n  /usr/bin/python3 "$SRCROOT/copy_private_model.py"\nfi\n'
modelPhase=obj('privateModel','isa=PBXShellScriptBuildPhase; buildActionMask=2147483647; files=(); inputPaths=(); outputPaths=(); alwaysOutOfDate=1; name="Private offline cardio model"; runOnlyForDeploymentPostprocessing=0; shellPath=/bin/sh; shellScript='+quoted(modelScript)+';')
appGroup=obj('appGroup','isa=PBXGroup; children='+arr(sourceRefs)+'; path=HomePulse; sourceTree="<group>";')
testGroup=obj('testGroup','isa=PBXGroup; children='+arr(testRefs)+'; path=HomePulseTests; sourceTree="<group>";')
uiGroup=obj('uiGroup','isa=PBXGroup; children='+arr(uiRefs)+'; path=HomePulseUITests; sourceTree="<group>";')
products=obj('products','isa=PBXGroup; children='+arr([appProduct,testProduct,uiProduct])+'; name=Products; sourceTree="<group>";')
main=obj('main','isa=PBXGroup; children='+arr([appGroup,testGroup,uiGroup,products])+'; sourceTree="<group>";')
def configs(prefix,settings):
 ids=[]
 for mode in ('Debug','Release'):
  merged={**settings,'SWIFT_OPTIMIZATION_LEVEL':'-Onone' if mode=='Debug' else '-O','DEBUG_INFORMATION_FORMAT':'dwarf' if mode=='Debug' else 'dwarf-with-dsym'}
  if mode=='Debug':merged['ENABLE_TESTABILITY']='YES';merged['SWIFT_ACTIVE_COMPILATION_CONDITIONS']='DEBUG'
  body='isa=XCBuildConfiguration; buildSettings={'+''.join(quoted(k)+'='+quoted(v)+';' for k,v in merged.items())+'}; name='+mode+';'
  ids.append(obj(prefix+mode,body))
 return obj(prefix+'List','isa=XCConfigurationList; buildConfigurations='+arr(ids)+'; defaultConfigurationIsVisible=0; defaultConfigurationName=Release;')
projConfig=configs('project',{'SDKROOT':'iphoneos','IPHONEOS_DEPLOYMENT_TARGET':'17.0','SWIFT_VERSION':'5.0','CLANG_ENABLE_MODULES':'YES','TARGETED_DEVICE_FAMILY':'1,2','SWIFT_STRICT_CONCURRENCY':'minimal'})
appConfig=configs('app',{'PRODUCT_NAME':'$(TARGET_NAME)','PRODUCT_BUNDLE_IDENTIFIER':'com.familos.homepulse','INFOPLIST_FILE':'HomePulse/Info.plist','GENERATE_INFOPLIST_FILE':'NO','CODE_SIGN_STYLE':'Automatic','LD_RUNPATH_SEARCH_PATHS':'$(inherited) @executable_path/Frameworks','SUPPORTED_PLATFORMS':'iphoneos iphonesimulator','ASSETCATALOG_COMPILER_APPICON_NAME':'AppIcon',
 'SWIFT_OBJC_BRIDGING_HEADER':'HomePulse/HomePulse-Bridging-Header.h','CLANG_CXX_LANGUAGE_STANDARD':'c++17','CLANG_CXX_LIBRARY':'libc++','GCC_ENABLE_CPP_EXCEPTIONS':'YES',
 'ENABLE_USER_SCRIPT_SANDBOXING':'NO','HEADER_SEARCH_PATHS[sdk=iphoneos*]':'$(inherited) $(SRCROOT)/Research/LibTorch/install/include $(SRCROOT)/Research/LibTorch/install/include/torch/csrc/api/include',
 'LIBRARY_SEARCH_PATHS[sdk=iphoneos*]':'$(inherited) $(SRCROOT)/Research/LibTorch/install/lib',
 'OTHER_LDFLAGS[sdk=iphoneos*]':'$(inherited) -force_load $(SRCROOT)/Research/LibTorch/install/lib/libtorch.a -force_load $(SRCROOT)/Research/LibTorch/install/lib/libtorch_cpu.a -lc10 -lXNNPACK -lcpuinfo -leigen_blas -lpthreadpool -lclog -lpytorch_qnnpack -lc++ -framework Accelerate'})
testConfig=configs('test',{'PRODUCT_NAME':'$(TARGET_NAME)','PRODUCT_BUNDLE_IDENTIFIER':'com.familos.homepulse.tests','GENERATE_INFOPLIST_FILE':'YES','TEST_HOST':'$(BUILT_PRODUCTS_DIR)/HomePulse.app/HomePulse','BUNDLE_LOADER':'$(TEST_HOST)','CODE_SIGN_STYLE':'Automatic'})
uiConfig=configs('ui',{'PRODUCT_NAME':'$(TARGET_NAME)','PRODUCT_BUNDLE_IDENTIFIER':'com.familos.homepulse.uitests','GENERATE_INFOPLIST_FILE':'YES','TEST_TARGET_NAME':'HomePulse','CODE_SIGN_STYLE':'Automatic'})
appID=ident('appTarget');projectID=ident('project')
proxy=obj('proxy','isa=PBXContainerItemProxy; containerPortal='+projectID+'; proxyType=1; remoteGlobalIDString='+appID+'; remoteInfo=HomePulse;')
dependency=obj('dependency','isa=PBXTargetDependency; target='+appID+'; targetProxy='+proxy+';')
obj('appTarget','isa=PBXNativeTarget; buildConfigurationList='+appConfig+'; buildPhases='+arr([appSources,frameworks,resources,modelPhase])+'; buildRules=(); dependencies=(); name=HomePulse; productName=HomePulse; productReference='+appProduct+'; productType="com.apple.product-type.application";')
testTarget=obj('testTarget','isa=PBXNativeTarget; buildConfigurationList='+testConfig+'; buildPhases='+arr([testSources])+'; buildRules=(); dependencies='+arr([dependency])+'; name=HomePulseTests; productName=HomePulseTests; productReference='+testProduct+'; productType="com.apple.product-type.bundle.unit-test";')
uiTarget=obj('uiTarget','isa=PBXNativeTarget; buildConfigurationList='+uiConfig+'; buildPhases='+arr([uiSources])+'; buildRules=(); dependencies='+arr([dependency])+'; name=HomePulseUITests; productName=HomePulseUITests; productReference='+uiProduct+'; productType="com.apple.product-type.bundle.ui-testing";')
obj('project','isa=PBXProject; attributes={BuildIndependentTargetsInParallel=YES; LastUpgradeCheck=2710; TargetAttributes={'+appID+'={CreatedOnToolsVersion=27.1;};'+testTarget+'={CreatedOnToolsVersion=27.1; TestTargetID='+appID+';};};}; buildConfigurationList='+projConfig+'; compatibilityVersion="Xcode 14.0"; developmentRegion=it; hasScannedForEncodings=0; knownRegions=(it,en,Base); mainGroup='+main+'; productRefGroup='+products+'; projectDirPath=""; projectRoot=""; targets='+arr([appID,testTarget,uiTarget])+';')
project=root/'HomePulse.xcodeproj';project.mkdir(exist_ok=True)
(project/'project.pbxproj').write_text('// !$*UTF8*$!\n{archiveVersion=1; classes={}; objectVersion=56; objects={\n'+''.join(i+'={'+v+'};\n' for i,v in objects.items())+'}; rootObject='+projectID+';}\n')
scheme=project/'xcshareddata/xcschemes';scheme.mkdir(parents=True,exist_ok=True)
(scheme/'HomePulse.xcscheme').write_text(f'''<?xml version="1.0" encoding="UTF-8"?><Scheme LastUpgradeVersion="2710" version="1.3"><BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{appID}" BuildableName="HomePulse.app" BlueprintName="HomePulse" ReferencedContainer="container:HomePulse.xcodeproj"/></BuildActionEntry></BuildActionEntries></BuildAction><TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"><Testables><TestableReference skipped="NO"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{testTarget}" BuildableName="HomePulseTests.xctest" BlueprintName="HomePulseTests" ReferencedContainer="container:HomePulse.xcodeproj"/></TestableReference><TestableReference skipped="NO"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{uiTarget}" BuildableName="HomePulseUITests.xctest" BlueprintName="HomePulseUITests" ReferencedContainer="container:HomePulse.xcodeproj"/></TestableReference></Testables></TestAction><LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" debugServiceExtension="internal" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{appID}" BuildableName="HomePulse.app" BlueprintName="HomePulse" ReferencedContainer="container:HomePulse.xcodeproj"/></BuildableProductRunnable></LaunchAction><ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES" useCustomWorkingDirectory="NO" debugDocumentVersioning="YES"/><AnalyzeAction buildConfiguration="Debug"/><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/></Scheme>''')
print('Generated',len(files),'Swift app files and',len(tests),'test files')
