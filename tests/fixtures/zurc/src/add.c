#include <Rinternals.h>

SEXP add_(SEXP a, SEXP b) { return Rf_ScalarReal(Rf_asReal(a) + Rf_asReal(b)); }
