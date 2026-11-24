threads <- function() .Call("threads_", PACKAGE = "zuromp")
total <- function(n) .Call("total_", as.integer(n), PACKAGE = "zuromp")
