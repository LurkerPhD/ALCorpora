// Reads "a b" and prints a table of physical quantities plus a DERIVED ratio.
// The declared field model compares the physical columns and skips the ratio.
#include <cstdio>
#include <string>
int main(int argc, char** argv) {
    if (argc < 2) return 1;
    FILE* f = std::fopen(argv[1], "r");
    if (!f) return 1;
    double a = 0, b = 0;
    if (std::fscanf(f, "%lf %lf", &a, &b) != 2) { std::fclose(f); return 1; }
    std::fclose(f);
    std::string in = argv[1];
    std::string out = in.substr(0, in.find_last_of('.')) + ".out";
    FILE* o = std::fopen(out.c_str(), "w");
    if (!o) return 1;
    std::fprintf(o, "QUANTITIES\n");
    std::fprintf(o, "1  %.6f  %.6f  %.6f\n", a, b, b != 0 ? a / b : 0.0);
    std::fprintf(o, "2  %.6f  %.6f  %.6f\n", a * 2, b * 2, b != 0 ? (a*2)/(b*2) : 0.0);
    std::fclose(o);
    return 0;
}
