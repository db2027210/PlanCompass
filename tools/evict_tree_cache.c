#define _XOPEN_SOURCE 700

#include <fcntl.h>
#include <ftw.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static unsigned long visited;
static unsigned long failed;

static int evict_file(const char *path, const struct stat *st, int kind,
                      struct FTW *entry) {
    (void)st;
    (void)entry;
    if (kind != FTW_F) {
        return 0;
    }
    ++visited;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        ++failed;
        return 0;
    }
    if (posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED) != 0) {
        ++failed;
    }
    close(fd);
    return 0;
}

int main(int argc, char **argv) {
    const char *prefix = "/disk1/resource_adaptive_mysql_grid_20260718/workers/";
    if (argc != 2 || strncmp(argv[1], prefix, strlen(prefix)) != 0) {
        fputs("refusing cache eviction outside the owned disk1 worker root\n", stderr);
        return 64;
    }
    if (nftw(argv[1], evict_file, 64, FTW_PHYS) != 0) {
        perror("nftw");
        return 1;
    }
    fprintf(stderr, "cache_evict visited=%lu failed=%lu\n", visited, failed);
    return failed ? 2 : 0;
}
