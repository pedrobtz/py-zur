// R's default C++ standard: no CXX_STD, so CXX from Makeconf.
#include <numeric>
#include <vector>

#define R_NO_REMAP
#include <Rinternals.h>

extern "C" SEXP cplusplus_() { return Rf_ScalarReal(__cplusplus); }

extern "C" SEXP total_(SEXP n) {
  std::vector<double> v(Rf_asInteger(n));
  std::iota(v.begin(), v.end(), 1.0);
  return Rf_ScalarReal(std::accumulate(v.begin(), v.end(), 0.0));
}
