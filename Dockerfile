# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for Playwright and Chrome
RUN apt-get update && apt-get install -y \
    wget \
    gosu \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright system dependencies for all browsers (requires root)
RUN playwright install-deps

# Create a non-root user to run the application
RUN groupadd -r mitmore_seo_crawl && useradd -r -g mitmore_seo_crawl -u 1000 mitmore_seo_crawl \
    && mkdir -p /home/mitmore_seo_crawl && chown -R mitmore_seo_crawl:mitmore_seo_crawl /home/mitmore_seo_crawl

# Copy application code
COPY --chown=mitmore_seo_crawl:mitmore_seo_crawl . .

# Create directory for user database if it doesn't exist
RUN mkdir -p /app/data && chown -R mitmore_seo_crawl:mitmore_seo_crawl /app/data

# Change ownership of the entire app directory
RUN chown -R mitmore_seo_crawl:mitmore_seo_crawl /app

# Install all Playwright browsers as non-root user (installs to /home/mitmore_seo_crawl/.cache/ms-playwright)
RUN gosu mitmore_seo_crawl playwright install

# Expose Flask port
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=main.py
ENV PYTHONUNBUFFERED=1

COPY docker-entrypoint.sh /usr/local/bin/mitmore-seo-crawl-entrypoint
RUN chmod +x /usr/local/bin/mitmore-seo-crawl-entrypoint

# Run the application
ENTRYPOINT ["mitmore-seo-crawl-entrypoint"]
CMD ["python", "main.py"]
