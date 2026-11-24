// CXX_STD = CXX20: a concept and std::span, C++20 only.
#include <numeric>
#include <span>
#include <vector>

#define R_NO_REMAP
#include <Rinternals.h>

template <typename T>
concept Addable = requires(T a) { a + a; };

template <Addable T>
T sum(std::span<const T> xs) {
  return std::accumulate(xs.begin(), xs.end(), T{});
}

extern "C" SEXP cplusplus_() { return Rf_ScalarReal(__cplusplus); }

extern "C" SEXP total_(SEXP n) {
  std::vector<double> v(Rf_asInteger(n));
  std::iota(v.begin(), v.end(), 1.0);
  return Rf_ScalarReal(sum(std::span<const double>(v)));
}
