/* OpenMP through $(SHLIB_OPENMP_CFLAGS) (src/Makevars). */
#include <omp.h>
#include <Rinternals.h>

SEXP threads_(void) {
  int n = 0;
#pragma omp parallel
  {
#pragma omp single
    n = omp_get_num_threads();
  }
  return Rf_ScalarInteger(n);
}

SEXP total_(SEXP n) {
  int m = Rf_asInteger(n);
  double s = 0;
#pragma omp parallel for reduction(+ : s)
  for (int i = 1; i <= m; i++) s += i;
  return Rf_ScalarReal(s);
}
