sumsq <- function(n) .Fortran("sumsq", n = as.integer(n), s = 0, PACKAGE = "zurf")$s
