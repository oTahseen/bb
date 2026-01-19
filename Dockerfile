FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y \
        git \
        build-essential \
        libssl-dev \
        zlib1g-dev \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt

# Clone official Telegram MTProxy
RUN git clone https://github.com/TelegramMessenger/MTProxy.git

WORKDIR /opt/MTProxy

# Build
RUN make

# Create FINAL config file (secret lives HERE)
RUN printf "proxy 0.0.0.0:3128 {\\n  secret %s;\\n}\\n" "$SECRET" > proxy.conf

EXPOSE 3128

# Start MTProxy (CONFIG FILE ONLY)
CMD ["sh", "-c", "cd /opt/MTProxy && ./objs/bin/mtproto-proxy -H 3128 -c 1024 proxy.conf"]
