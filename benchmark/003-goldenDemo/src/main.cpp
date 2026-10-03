// Reads an input file of two integers and writes their sum to <stem>.out.
#include <string>
#include <cstdio>
#include <cstdlib>
int main(int argc, char** argv) {
    if (argc < 2) return 1;
    FILE* f = std::fopen(argv[1], "r");
    if (!f) return 1;
    long a = 0, b = 0;
    if (std::fscanf(f, "%ld %ld", &a, &b) != 2) { std::fclose(f); return 1; }
    std::fclose(f);
    std::string in = argv[1];
    std::string out = in.substr(0, in.find_last_of('.')) + ".out";
    FILE* o = std::fopen(out.c_str(), "w");
    if (!o) return 1;
    std::fprintf(o, "%ld\n", a + b);
    std::fclose(o);
    return 0;
}
