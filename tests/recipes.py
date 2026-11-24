"""Commands the README documents, as the tests run them.

This module is the single place the tests take them from; the README's
copy is checked against it character for character (roadmap stage 17).
"""

# What points pak at P3M for one R session (design §6.6, D9). zur itself
# sets no repository.
PPM_REPO_ADD = 'pak::repo_add(CRAN = "PPM@latest")'

# Design §6.6: pak from its own repository, then a package through pak
# from P3M. The install line of zur itself comes before these in the
# README.
PAK_RECIPE = """\
Rscript -e 'install.packages("pak", repos = sprintf(
  "https://r-lib.github.io/p/pak/stable/%s/%s/%s",
  .Platform$pkgType, R.version$os, R.version$arch))'
Rscript -e 'pak::repo_add(CRAN = "PPM@latest"); pak::pkg_install("data.table")'
"""

assert PPM_REPO_ADD in PAK_RECIPE


def pak_install(*packages):
    """R code installing ``packages`` the way the recipe does."""
    refs = ", ".join(f'"{p}"' for p in packages)
    return f"{PPM_REPO_ADD}; pak::pkg_install(c({refs}))"
