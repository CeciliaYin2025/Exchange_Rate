# === Stage 1: uv binary ===
FROM ghcr.io/astral-sh/uv:latest AS uv

# === Stage 2: Runtime image ===
FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

# Timezone
ENV TZ=Asia/Taipei
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# System dependencies: CJK fonts, unixodbc, libaio (for Oracle), wget, gnupg
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    unixodbc-dev \
    libaio1t64 \
    wget \
    gnupg \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1

# Google Chrome (for Selenium)
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Oracle Instant Client 23
RUN mkdir -p /opt/oracle && cd /opt/oracle \
    && wget -q https://download.oracle.com/otn_software/linux/instantclient/2370000/instantclient-basic-linux.x64-23.7.0.25.01.zip \
    && unzip instantclient-basic-linux.x64-23.7.0.25.01.zip \
    && rm instantclient-basic-linux.x64-23.7.0.25.01.zip \
    && echo /opt/oracle/instantclient_23_7 > /etc/ld.so.conf.d/oracle-instantclient.conf \
    && ldconfig

ENV LD_LIBRARY_PATH=/opt/oracle/instantclient_23_7:${LD_LIBRARY_PATH}
ENV ORACLE_CLIENT_LIB_DIR=/opt/oracle/instantclient_23_7
ENV PATH="/app/.venv/bin:${PATH}"

# Copy uv from builder stage
COPY --from=uv /uv /usr/local/bin/uv

# Working directory
WORKDIR /app

# Install Python dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Install Playwright browsers (Chromium only)
RUN uv run playwright install chromium

# Entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["bank"]
