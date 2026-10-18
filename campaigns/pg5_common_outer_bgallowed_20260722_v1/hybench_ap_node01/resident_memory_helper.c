#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

static volatile sig_atomic_t keep_running = 1;

static void handle_signal(int signal_number)
{
    (void) signal_number;
    keep_running = 0;
}

int main(int argc, char **argv)
{
    char *end = NULL;
    unsigned long long requested_bytes;
    size_t bytes;
    long page_size;
    unsigned char *region;
    size_t offset;

    if (argc != 2) {
        fprintf(stderr, "usage: %s BYTES\n", argv[0]);
        return 64;
    }

    errno = 0;
    requested_bytes = strtoull(argv[1], &end, 10);
    if (errno != 0 || end == argv[1] || *end != '\0' || requested_bytes == 0 ||
            requested_bytes > (unsigned long long) SIZE_MAX) {
        fprintf(stderr, "invalid byte count: %s\n", argv[1]);
        return 65;
    }
    bytes = (size_t) requested_bytes;
    page_size = sysconf(_SC_PAGESIZE);
    if (page_size <= 0) {
        perror("sysconf");
        return 66;
    }

    region = mmap(NULL, bytes, PROT_READ | PROT_WRITE,
            MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (region == MAP_FAILED) {
        perror("mmap");
        return 67;
    }
    for (offset = 0; offset < bytes; offset += (size_t) page_size) {
        region[offset] = (unsigned char) (offset / (size_t) page_size);
    }
    region[bytes - 1] = 1;

    signal(SIGTERM, handle_signal);
    signal(SIGINT, handle_signal);
    printf("resident_memory_ready pid=%ld bytes=%zu page_size=%ld\n",
            (long) getpid(), bytes, page_size);
    fflush(stdout);

    while (keep_running) {
        pause();
    }

    if (munmap(region, bytes) != 0) {
        perror("munmap");
        return 68;
    }
    return 0;
}
