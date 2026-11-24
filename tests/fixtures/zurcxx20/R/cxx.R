cplusplus <- function() .Call("cplusplus_", PACKAGE = "zurcxx20")
total <- function(n) .Call("total_", as.integer(n), PACKAGE = "zurcxx20")
