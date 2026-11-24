/* Minimal compatibility for the reviewed offline ecore path, not full Bionic. */
#include <stdarg.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
unsigned char __sF[1024]; /* Opaque only: the Bionic FILE ABI is NOT implemented. */
int *__errno(void) { return &errno; }
int __android_log_print(int priority, const char *tag, const char *format, ...) {
    (void)priority; (void)tag; (void)format; return 0;
}
void android_set_abort_message(const char *message) { (void)message; }
void __assert2(const char *file, int line, const char *function, const char *message) {
    (void)file; (void)line; (void)function; (void)message; abort();
}
size_t __strlen_chk(const char *string, size_t maximum) {
    size_t length = strnlen(string, maximum); if (length >= maximum) abort(); return length;
}
int __system_property_get(const char *name, char *value) { (void)name; if (value) value[0] = 0; return 0; }
