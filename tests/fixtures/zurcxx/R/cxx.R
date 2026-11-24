cplusplus <- function() .Call("cplusplus_", PACKAGE = "zurcxx")
total <- function(n) .Call("total_", as.integer(n), PACKAGE = "zurcxx")
