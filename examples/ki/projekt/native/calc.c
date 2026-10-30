#include <stdio.h>

struct stats {
    int count;
};

int count_words(const char *text)
{
    int n = 0;
    for (const char *p = text; *p; p++) {
        if (*p == ' ') {
            n++;
        }
    }
    return n + 1;
}
