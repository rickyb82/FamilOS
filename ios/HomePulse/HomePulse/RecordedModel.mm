#import "RecordedModel.h"
#import <TargetConditionals.h>
#if !TARGET_OS_SIMULATOR && __has_include(<torch/script.h>)
#define HP_HAS_TORCH 1
#include <torch/script.h>
#include <ATen/Parallel.h>
#include <memory>
#endif

@implementation HPRecordedModel {
#ifdef HP_HAS_TORCH
    std::unique_ptr<torch::jit::script::Module> _module;
#endif
}
static void HPError(NSError **error, NSString *message) {
    if (error) *error=[NSError errorWithDomain:@"HomePulse.RecordedModel" code:1
                    userInfo:@{NSLocalizedDescriptionKey:message}];
}
- (nullable instancetype)initWithPath:(NSString *)path error:(NSError **)error {
    self=[super init]; if (!self) return nil;
#ifdef HP_HAS_TORCH
    try {
        static dispatch_once_t once;
        dispatch_once(&once, ^{ at::set_num_threads(1); at::set_num_interop_threads(1); });
        _module=std::make_unique<torch::jit::script::Module>(torch::jit::load(path.UTF8String,torch::kCPU));
        _module->eval();
        return self;
    } catch (const std::exception &) { HPError(error,@"Modello cardio locale non caricabile"); return nil; }
#else
    HPError(error,@"Il calcolo cardio locale richiede la build iPhone con il modello privato"); return nil;
#endif
}
- (nullable NSDictionary<NSString *,id> *)inferPPG:(NSData *)ppg
    accel:(NSData *)accel intensity:(NSData *)intensity timeline:(NSData *)timeline
    activity:(NSInteger)activity demographics:(NSData *)demographics error:(NSError **)error {
#ifdef HP_HAS_TORCH
    if (!_module || ppg.length%120 || accel.length%120 || intensity.length%4 ||
        intensity.length!=timeline.length || demographics.length!=24 ||
        ppg.length>120*3600 || accel.length>120*10800 || intensity.length>4*10800) {
        HPError(error,@"Forma o dimensione delle tracce cardio non valida"); return nil;
    }
    try {
        torch::NoGradGuard guard;
        auto blob=[](NSData *data,std::vector<int64_t> shape) {
            // Clone: the model clamps demographics in place. NSData stays immutable.
            if (!data.length) return torch::empty(shape,torch::kFloat32);
            return torch::from_blob(const_cast<void *>(data.bytes),shape,torch::kFloat32).clone();
        };
        std::vector<torch::jit::IValue> args={
            blob(ppg,{(int64_t)ppg.length/120,5,6}),blob(accel,{(int64_t)accel.length/120,5,6}),
            blob(intensity,{(int64_t)intensity.length/4}),blob(timeline,{(int64_t)timeline.length/4}),
            torch::tensor({(float)activity}),blob(demographics,{6})};
        auto out=_module->forward(args).toTuple();
        if (out->elements().size()!=4) { HPError(error,@"Uscita modello cardio inattesa"); return nil; }
        auto bytes=[](const torch::jit::IValue &value) {
            auto t=value.toTensor().to(torch::kCPU).to(torch::kFloat32).contiguous();
            return [NSData dataWithBytes:t.data_ptr<float>() length:t.numel()*sizeof(float)];
        };
        return @{@"time":bytes(out->elements()[0]),@"hr":bytes(out->elements()[1]),
                 @"signal_check":@(out->elements()[2].toDouble()),@"quality":bytes(out->elements()[3])};
    } catch (const std::exception &) { HPError(error,@"Elaborazione locale cardio non riuscita; tracce conservate"); return nil; }
#else
    HPError(error,@"Calcolo del modello non disponibile nel simulatore"); return nil;
#endif
}
@end
