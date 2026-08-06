# Custom manylinux images

The cache workflow builds these images on GitHub-hosted Linux runners and
stores their Docker layers in GitHub Actions. Release jobs restore those layers
and load the resulting image. No local Docker installation or container
registry setup is required.

The base-image tags match the images pinned by cibuildwheel 3.4.1. When
upgrading cibuildwheel, update both Dockerfiles to its corresponding pinned
`manylinux_2_28` tags. A push to `development` or `master` that changes these
files automatically warms the x86-64 and ARM caches. A weekly scheduled run
keeps both the image layers and compiled OpenAL runtimes from aging out of the
GitHub Actions cache.

The marker at `/opt/pyalsoft-manylinux-ready` lets cibuildwheel skip its
fallback package installation. Direct local cibuildwheel runs using the
official image continue to install the packages as needed.
