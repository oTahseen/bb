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

RUN git clone https://github.com/TelegramMessenger/MTProxy.git
WORKDIR /opt/MTProxy
RUN make

# Minimal valid config (NO secret here)
RUN echo "proxy 0.0.0.0:3128;" > proxy.conf

EXPOSE 3128

# IMPORTANT:
# - secret via -S
# - limit connections to avoid rlimit crash
# - config file LAST
CMD ["sh", "-c", "cd /opt/MTProxy && ./objs/bin/mtproto-proxy -H 3128 -S $SECRET -c 1024 proxy.conf"]
