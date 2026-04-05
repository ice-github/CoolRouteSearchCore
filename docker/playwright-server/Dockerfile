ARG PLAYWRIGHT_BASE_IMAGE=mcr.microsoft.com/playwright:v1.58.0-noble
FROM ${PLAYWRIGHT_BASE_IMAGE}

ARG PLAYWRIGHT_VERSION=1.58.0

# The official base image ships browsers and system dependencies, but not a
# ready-to-run Playwright server process for our pinned version.
RUN PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install -g "playwright@${PLAYWRIGHT_VERSION}" \
    && playwright --version

WORKDIR /home/pwuser
USER pwuser
