#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN
/// Full JIT interpreter; model and runtime are supplied privately at build time.
@interface HPRecordedModel : NSObject
- (nullable instancetype)initWithPath:(NSString *)path error:(NSError **)error;
- (nullable NSDictionary<NSString *,id> *)inferPPG:(NSData *)ppg
    accel:(NSData *)accel intensity:(NSData *)intensity timeline:(NSData *)timeline
    activity:(NSInteger)activity demographics:(NSData *)demographics error:(NSError **)error;
@end
NS_ASSUME_NONNULL_END
