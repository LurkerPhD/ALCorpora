// Reads n from <stem>.in, adds any running total in <stem>.st (handed forward
// by the harness), and writes the new total to <stem>.out and <stem>.st.
#include <cstdio>
#include <string>
static long read_long(const char* p, long dflt) {
    FILE* f = std::fopen(p, "r"); if (!f) return dflt;
    long v = dflt; if (std::fscanf(f, "%ld", &v) != 1) v = dflt;
    std::fclose(f); return v;
}
int main(int argc, char** argv) {
    if (argc < 2) return 1;
    std::string in = argv[1];
    std::string stem = in.substr(0, in.find_last_of('.'));
    long n = read_long(in.c_str(), 0);
    long total = n + read_long((stem + ".st").c_str(), 0);
    FILE* o = std::fopen((stem + ".out").c_str(), "w");
    if (!o) return 1; std::fprintf(o, "%ld\n", total); std::fclose(o);
    FILE* s = std::fopen((stem + ".st").c_str(), "w");
    if (!s) return 1; std::fprintf(s, "%ld\n", total); std::fclose(s);
    return 0;
}
