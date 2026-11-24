// CXX_STD = CXX26, the newest standard Makeconf offers. C++23 is 202302L;
// any C++26 compiler reports a later value.
#include <numeric>
#include <vector>

#define R_NO_REMAP
#include <Rinternals.h>

static_assert(__cplusplus > 202302L, "not compiled as C++26");

extern "C" SEXP cplusplus_() { return Rf_ScalarReal(__cplusplus); }

extern "C" SEXP total_(SEXP n) {
  std::vector<double> v(Rf_asInteger(n));
  std::iota(v.begin(), v.end(), 1.0);
  return Rf_ScalarReal(std::accumulate(v.begin(), v.end(), 0.0));
}
