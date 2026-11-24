"""Commands the README documents, as the tests run them.

This module is the single place the tests take them from; the README's
copy is checked against it character for character (roadmap stage 17).
"""

# Design §6.6: pak from its own repository, then a package through pak,
# with no `repos` and no user-agent setting. The install line of zur
# itself comes before these in the README.
PAK_RECIPE = """\
Rscript -e 'install.packages("pak", repos = sprintf(
  "https://r-lib.github.io/p/pak/stable/%s/%s/%s",
  .Platform$pkgType, R.version$os, R.version$arch))'
Rscript -e 'pak::pkg_install("data.table")'
"""
