#define _XOPEN_SOURCE 700

#include <errno.h>
#include <fcntl.h>
#include <ftw.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static uint64_t g_files = 0;
static uint64_t g_bytes = 0;
static uint64_t g_errors = 0;
static uint64_t g_max_files = 0;
static uint64_t g_max_bytes = 0;

static int visit_file(const char *path, const struct stat *st, int typeflag, struct FTW *ftwbuf) {
    (void)ftwbuf;
    if (g_max_files && g_files >= g_max_files) {
        return 1;
    }
    if (g_max_bytes && g_bytes >= g_max_bytes) {
        return 1;
    }
    if (typeflag != FTW_F || st == NULL) {
        return 0;
    }

    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        g_errors++;
        return 0;
    }
    int rc = posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED);
    if (rc != 0) {
        g_errors++;
    } else {
        g_files++;
        if (st->st_size > 0) {
            g_bytes += (uint64_t)st->st_size;
        }
    }
    close(fd);
    return 0;
}

static uint64_t parse_limit(const char *value) {
    if (value == NULL || value[0] == '\0') {
        return 0;
    }
    char *end = NULL;
    unsigned long long parsed = strtoull(value, &end, 10);
    if (end == value) {
        return 0;
    }
    return (uint64_t)parsed;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s PATH [MAX_FILES] [MAX_BYTES]\n", argv[0]);
        return 2;
    }
    const char *root = argv[1];
    g_max_files = argc >= 3 ? parse_limit(argv[2]) : 0;
    g_max_bytes = argc >= 4 ? parse_limit(argv[3]) : 0;

    errno = 0;
    int rc = nftw(root, visit_file, 32, FTW_PHYS);
    if (rc < 0) {
        g_errors++;
    }

    printf("{\"ok\":true,\"files\":%llu,\"bytes\":%llu,\"errors\":%llu,\"rc\":%d}\n",
           (unsigned long long)g_files,
           (unsigned long long)g_bytes,
           (unsigned long long)g_errors,
           rc);
    return g_errors ? 1 : 0;
}
