#define _XOPEN_SOURCE 700

#include <errno.h>
#include <fcntl.h>
#include <ftw.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static uint64_t regular_files;
static uint64_t advised_bytes;
static uint64_t skipped_files;
static uint64_t failed_files;

static int advise_file(const char *path, const struct stat *st, int type,
                       struct FTW *unused) {
    (void)unused;
    if (type != FTW_F || !S_ISREG(st->st_mode)) {
        return 0;
    }
    regular_files++;
    if (st->st_size == 0) {
        skipped_files++;
        return 0;
    }
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        fprintf(stderr, "open failed: %s: %s\n", path, strerror(errno));
        failed_files++;
        return 0;
    }
    int result = posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED);
    if (result != 0) {
        fprintf(stderr, "posix_fadvise failed: %s: %s\n", path,
                strerror(result));
        failed_files++;
    } else {
        advised_bytes += (uint64_t)st->st_size;
    }
    close(fd);
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2 || argv[1][0] != '/') {
        fprintf(stderr, "usage: %s /absolute/datadir\n", argv[0]);
        return 2;
    }
    if (nftw(argv[1], advise_file, 32, FTW_PHYS | FTW_MOUNT) != 0) {
        fprintf(stderr, "nftw failed: %s: %s\n", argv[1], strerror(errno));
        return 3;
    }
    printf("{\"regular_files\":%" PRIu64
           ",\"advised_bytes\":%" PRIu64
           ",\"skipped_files\":%" PRIu64
           ",\"failed_files\":%" PRIu64 "}\n",
           regular_files, advised_bytes, skipped_files, failed_files);
    return failed_files == 0 ? 0 : 4;
}
